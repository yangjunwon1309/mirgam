"""Local UI gateway. All business reads/writes go through the central API."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import threading
from zoneinfo import ZoneInfo

from flask import Flask, Response, flash, jsonify, redirect, render_template, request, session, url_for
from .api_client import APIClient, APIClientError
from .order_save_dialog import choose_order_export_path


def today():
    return datetime.now(ZoneInfo('Asia/Seoul')).date()


def create_app(api_url=None, testing=False, transport=None):
    template_dir = (Path(sys._MEIPASS) / 'apps/templates' if getattr(sys, 'frozen', False)
                    else Path(__file__).parent / 'templates')
    app = Flask(__name__, template_folder=str(template_dir), static_folder=None)
    app.config.update(SECRET_KEY=os.environ.get('MIRGAM_CLIENT_SECRET') or secrets.token_bytes(32),
                      TESTING=testing, MAX_CONTENT_LENGTH=10 * 1024 * 1024,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
                      ORDER_EXPORT_DIRECTORY=str(Path.home() / 'Downloads'))
    api = APIClient(api_url, transport)
    app.extensions.update(api_client=api, login_states={})
    state_lock = threading.RLock()

    def state():
        value = app.extensions['login_states'].get(session.get('client_id'))
        if value is None:
            raise APIClientError('로그인한 후 이용해 주세요.', 401, code='unauthorized')
        return value

    def central(method, path, **kwargs):
        return api.request(method, '/api/v1' + path, token=state()['token'], **kwargs)

    def csrf_token():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_urlsafe(32)
        return session['csrf']

    @app.before_request
    def protect_local_ui():
        if request.host.split(':')[0] not in ('localhost', '127.0.0.1'):
            return 'Invalid local host', 400
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('Origin')
            if (origin and origin.rstrip('/') != request.host_url.rstrip('/')) or request.headers.get('Sec-Fetch-Site') == 'cross-site':
                return 'Invalid origin', 403
            supplied = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token', '')
            if not supplied or not secrets.compare_digest(supplied, session.get('csrf', '')):
                if request.is_json:
                    return jsonify(message='화면을 새로고침한 후 다시 시도해 주세요.'), 403
                flash('화면을 새로고침한 후 다시 시도해 주세요.')
                return redirect(url_for('login'))
        if request.endpoint not in ('init', 'login', 'contact', 'status') and request.endpoint is not None:
            state()

    @app.context_processor
    def context():
        return {'csrf_token': csrf_token(), 'current_account': app.extensions['login_states'].get(session.get('client_id'), {}).get('name', '')}

    @app.after_request
    def html_security(response):
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'same-origin'})
        if response.mimetype == 'text/html':
            html, token = response.get_data(as_text=True), csrf_token()
            html = html.replace('<body>', '<body>' + render_template('session_bar.html'), 1)
            def add_token(match):
                opening = match.group(0)
                if re.search(r'method\s*=\s*[\"\x27]?post\b', opening, re.I):
                    return opening + f'<input type="hidden" name="csrf_token" value="{token}">'
                return opening
            html = re.sub(r'<form\b[^>]*>', add_token, html, flags=re.I)
            guard = '<script>const mirgamFetch=window.fetch;window.fetch=(url,options={})=>{if(["POST","PATCH","DELETE","PUT"].includes((options.method||"GET").toUpperCase())&&new URL(url,location.href).origin===location.origin){options.headers=new Headers(options.headers||{});options.headers.set("X-CSRF-Token",' + json.dumps(token) + ')}return mirgamFetch(url,options)};</script>'
            html = html.replace('</head>', guard + '</head>', 1)
            response.set_data(html)
        return response

    @app.errorhandler(APIClientError)
    def api_error(error):
        if request.is_json or request.path == '/customers/search':
            return jsonify(message=error.message, details=error.details), error.status
        if error.status == 401:
            app.extensions['login_states'].pop(session.get('client_id'), None)
            session.pop('client_id', None)
            flash(error.message)
            return redirect(url_for('login'))
        return render_template('server_error.html', message=error.message), error.status

    @app.get('/')
    def init():
        return redirect(url_for('order') if session.get('client_id') in app.extensions['login_states'] else url_for('login'))

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            try:
                api.health()
                result = api.request('POST', '/api/v1/auth/login', json={
                    'name': request.form.get('farm-name', ''), 'password': request.form.get('password', '')})['data']
            except APIClientError as error:
                flash(error.message)
                return render_template('login.html'), error.status
            app.extensions['login_states'].pop(session.get('client_id'), None)
            session.clear()
            identifier = secrets.token_urlsafe(32)
            session['client_id'] = identifier
            app.extensions['login_states'][identifier] = {'token': result['token'], 'name': result['account']['name'], 'pending': {}, 'items': []}
            return redirect(url_for('order'))
        return render_template('login.html')

    @app.post('/logout')
    def logout():
        try:
            central('POST', '/auth/logout', json={})
        finally:
            app.extensions['login_states'].pop(session.get('client_id'), None)
            session.clear()
        return redirect(url_for('login'))

    @app.get('/status')
    def status():
        try:
            api.health()
            return jsonify(connected=True)
        except APIClientError:
            return jsonify(connected=False), 503

    @app.get('/contact')
    def contact():
        return render_template('contact.html')

    @app.post('/sms-connection')
    def save_sms_connection():
        if request.form.get('confirm_privacy') != 'yes':
            flash('개인 SMS가 중앙 서버에 전달될 수 있다는 안내를 확인해 주세요.')
            return redirect(url_for('mypage'))
        payload = {key: request.form.get(key, '').strip() for key in
                   ('base_url', 'username', 'password', 'device_id', 'phone_number', 'signing_key', 'auto_ack_text')}
        payload['sim_number'] = request.form.get('sim_number', '1')
        payload['auto_ack_enabled'] = request.form.get('auto_ack_enabled') == 'yes'
        result = central('PUT', '/sms-gate/connection', json=payload)
        flash('SMS Gateway 기기를 연결했습니다. 테스트 문자를 보낸 뒤 문자 주문 화면에서 수신을 확인하세요.')
        return redirect(url_for('mypage'))

    @app.post('/sms-connection/disconnect')
    def disconnect_sms_connection():
        central('DELETE', '/sms-gate/connection')
        flash('SMS Gateway 연결을 해제했습니다.')
        return redirect(url_for('mypage'))

    @app.get('/sms-orders')
    def sms_orders():
        state_filter = request.args.get('state', 'review')
        if state_filter not in ('review', 'confirmed', 'ignored', 'all'):
            state_filter = 'review'
        result = central('GET', '/sms/inbound', params={'state': state_filter, 'page': 1, 'page_size': 50})
        products = central('GET', '/products')['data']
        outbox = central('GET', '/sms/outbox', params={'page': 1, 'page_size': 20})['data']
        return render_template('sms_orders.html', messages=result['data'], products=products,
                               selected_state=state_filter, total=result['total'], outbox=outbox)

    @app.post('/sms-orders/<int:identifier>/confirm')
    def confirm_sms_order(identifier):
        values = {'product_id': request.form.get('product_id'), 'quantity': request.form.get('quantity', '').strip(),
                  'date': request.form.get('date', '').strip()}
        customer_id = request.form.get('customer_id', '').strip()
        if customer_id:
            values['customer_id'] = int(submitted_id('customer_id'))
        else:
            values['customer'] = {key: request.form.get(key, '').strip()
                                  for key in ('name', 'ph', 'address')}
        if not re.fullmatch(r'[1-9][0-9]{0,18}', values['product_id']):
            raise APIClientError('주문 상품을 선택해 주세요.', 422)
        values['product_id'] = int(values['product_id'])
        central('POST', f'/sms/inbound/{identifier}/confirm', json=values)
        flash('문자 주문을 확정하고 기존 주문 내역에 저장했습니다.')
        return redirect(url_for('sms_orders'))

    @app.post('/sms-orders/<int:identifier>/ignore')
    def ignore_sms_order(identifier):
        central('POST', f'/sms/inbound/{identifier}/ignore', json={})
        flash('선택한 문자를 주문 검토 목록에서 제외했습니다.')
        return redirect(url_for('sms_orders'))

    def render_order_form(drafts=None, request_id=None, cached=False, committed=False, uncertain=False):
        login_state = state()
        if not cached:
            login_state['items'] = central('GET', '/products')['data']
        return render_template('order.html', items=login_state['items'], today=today().isoformat(),
                               draft_orders=drafts or [], request_id=request_id or secrets.token_hex(16), committed=committed, uncertain=uncertain)

    @app.get('/order')
    def order():
        return render_order_form()

    @app.get('/customers/search')
    def search_customers():
        keyword = request.args.get('q', '').strip()
        if not keyword:
            return jsonify([])
        return jsonify(central('GET', '/customers', params={'q': keyword, 'page_size': 12})['data'])

    @app.post('/customers/new')
    def new_customer():
        values = request.get_json(silent=True) or {}
        if not isinstance(values, dict):
            raise APIClientError('고객 입력 내용을 확인해 주세요.', 422)
        result = central('POST', '/customers', json={'name': values.get('new_name'), 'ph': values.get('new_phone'), 'address': values.get('new_address')})['data']
        return jsonify(result), 201 if result['created'] else 200

    @app.post('/order')
    def save_order():
        request_id = request.form.get('request_id', '')
        if not re.fullmatch(r'[a-f0-9]{32}', request_id):
            raise APIClientError('주문 저장 화면을 새로 열어 주세요.', 422)
        try:
            drafts = json.loads(request.form.get('orders', '[]'))
            if not isinstance(drafts, list) or not drafts or len(drafts) > 1000 or any(not isinstance(row, dict) for row in drafts):
                raise ValueError()
        except (ValueError, TypeError):
            raise APIClientError('저장할 주문 정보를 확인해 주세요.', 422)
        login_state = state()
        with state_lock:
            pending = login_state['pending'].get(request_id)
            if pending is None:
                products = {row['item']: row['id'] for row in login_state['items']}
                payload = [{'customer_id': row.get('customer_id'), 'product_id': products.get(row.get('item')),
                            **{field: row.get(field, '') for field in ('quantity', 'date', 'sender_name', 'sender_ph', 'sender_address')}} for row in drafts]
                pending = {'drafts': drafts, 'payload': payload, 'batch_id': None, 'uncertain': False}
                login_state['pending'][request_id] = pending
            elif not pending['batch_id'] and not pending['uncertain']:
                login_state['pending'].pop(request_id)
                return save_order()
            drafts = pending['drafts']
            picker = app.config.get('ORDER_SAVE_DIALOG', choose_order_export_path)
            temporary = None
            try:
                if pending['uncertain']:
                    try:
                        pending['batch_id'] = central('GET', '/order-batches/by-request/' + request_id)['data']['id']
                        pending['uncertain'] = False
                    except APIClientError as error:
                        if error.status != 404:
                            raise
                destination = picker(app.config['ORDER_EXPORT_DIRECTORY'], today().strftime('%Y_%m_%d') + '.csv')
                if not destination:
                    flash('CSV 저장을 취소했습니다. 주문은 이미 서버에 저장되었습니다.' if pending['batch_id'] else '저장을 취소했습니다. 입력한 주문은 유지됩니다.')
                    return render_order_form(drafts, request_id, cached=True, committed=bool(pending['batch_id']))
                with tempfile.NamedTemporaryFile(dir=Path(destination).parent, delete=False) as file:
                    temporary = file.name
                if not pending['batch_id']:
                    pending['uncertain'] = True
                    pending['batch_id'] = central('POST', '/order-batches', json={'request_id': request_id, 'orders': pending['payload']})['data']['id']
                    pending['uncertain'] = False
                content = central('GET', f"/order-batches/{pending['batch_id']}/export.csv", raw=True)
                with open(temporary, 'wb') as file:
                    file.write(content)
                os.replace(temporary, destination)
                temporary = None
                flash(f'주문 {len(drafts)}건을 중앙 서버에 저장했습니다. CSV: {destination}')
                pending['complete'] = True
                return redirect(url_for('order'))
            except APIClientError as error:
                if error.status in (400, 401, 403, 404, 409, 422):
                    pending['uncertain'] = False
                detail = '; '.join(f"{row.get('row', '?')}번: {row.get('message', '')}" for row in (error.details or []) if isinstance(row, dict))
                flash(('주문은 서버에 저장됨 / CSV 저장 실패. CSV만 다시 저장하세요. ' if pending['batch_id'] else '') + error.message + (' ' + detail if detail else ''))
                return render_order_form(drafts, request_id, cached=True, committed=bool(pending['batch_id']), uncertain=pending['uncertain'])
            except (OSError, ValueError, subprocess.SubprocessError):
                flash('주문은 서버에 저장됨 / CSV 저장 실패. CSV만 다시 저장하세요.' if pending['batch_id'] else '저장 위치를 확인해 주세요. 입력한 주문은 유지됩니다.')
                return render_order_form(drafts, request_id, cached=True, committed=bool(pending['batch_id']))
            finally:
                if temporary:
                    try:
                        Path(temporary).unlink(missing_ok=True)
                    except OSError:
                        app.logger.warning('Order export temporary file cleanup deferred')

    def submitted_id(field):
        value = request.form.get(field, '')
        if not re.fullmatch(r'[1-9][0-9]{0,18}', value):
            raise APIClientError('선택한 항목을 다시 확인해 주세요.', 422)
        return value

    def list_params():
        return {'q': request.args.get('q', '').strip(), 'page': request.args.get('page', '1'), 'page_size': 50}

    @app.get('/order_view')
    def order_view():
        params = dict(list_params(), date=request.args.get('date', ''))
        result = central('GET', '/orders', params=params)
        dates = central('GET', '/orders/dates')['data']
        return render_template('order_view.html', orders=result['data'], order_count=result['total'], available_dates=[row['date'] for row in dates],
                               selected_date=params['date'], keyword=params['q'], page=result['page'], pages=max(1, (result['total'] + 49) // 50))

    @app.post('/order_view')
    def delete_or_export_order():
        day = request.form.get('return_to_date', request.form.get('download_date', ''))
        keyword = request.form.get('q', '')
        if 'order_id' in request.form:
            central('DELETE', '/orders/' + submitted_id('order_id'))
            flash('선택한 주문 한 건을 삭제했습니다.')
        else:
            content = central('GET', '/orders/export.csv', params={'date': day, 'q': keyword}, raw=True)
            filename = today().strftime('%Y%m%d') + ('_' + day.replace('-', '') if day else '') + '_order.csv'
            return Response(content, content_type='text/csv; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="{filename}"'})
        return redirect(url_for('order_view', date=day, q=keyword))

    @app.get('/customer')
    def view_customer():
        params = dict(list_params(), sort=request.args.get('sort', 'name'))
        result = central('GET', '/customers', params=params)
        return render_template('customer.html', customers=result['data'], selected_sort=params['sort'], keyword=params['q'],
                               page=result['page'], pages=max(1, (result['total'] + 49) // 50), total=result['total'])

    @app.post('/customer')
    def manage_customer():
        if 'customer_id' in request.form:
            identifier = submitted_id('customer_id')
            if request.form.get('action') == 'edit':
                central('PATCH', '/customers/' + identifier, json={'name': request.form.get('new_name'), 'ph': request.form.get('new_phone'), 'address': request.form.get('new_address')})
                flash('고객 정보를 수정했습니다. 이전 주문의 배송 정보는 유지됩니다.')
            else:
                central('DELETE', '/customers/' + identifier)
                flash('선택한 고객을 삭제했습니다. 이전 주문은 유지됩니다.')
        else:
            result = central('POST', '/customers', json={'name': request.form.get('new_name'), 'ph': request.form.get('new_phone'), 'address': request.form.get('new_address')})['data']
            flash('고객을 등록했습니다.' if result['created'] else '같은 이름·전화번호·주소의 고객이 이미 있습니다.')
        return redirect(url_for('view_customer'))

    @app.get('/customer/export')
    def customer_export():
        return Response(central('GET', '/customers/export.csv', raw=True), content_type='text/csv; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="customers.csv"'})

    @app.route('/mypage', methods=['GET', 'POST'])
    def mypage():
        if request.method == 'POST':
            if 'product_id' in request.form:
                identifier = submitted_id('product_id')
                if request.form.get('action') == 'edit':
                    central('PATCH', '/products/' + identifier, json={'price': request.form.get('price', '')})
                else:
                    central('DELETE', '/products/' + identifier)
            else:
                central('POST', '/products', json={'item': request.form.get('add_item'), 'price': request.form.get('add_price', '')})
            return redirect(url_for('mypage'))
        connection = central('GET', '/sms-gate/connection')['data']
        return render_template('mypage.html', items=central('GET', '/products')['data'], sms_connection=connection)

    @app.post('/upload')
    def upload():
        file = request.files.get('file')
        if file is None:
            raise APIClientError('파일을 선택해 주세요.', 422)
        result = central('POST', '/customer-imports', files={'file': (file.filename, file.stream, file.mimetype)})['data']
        return render_template('customer_import.html', info=result, preview=None)

    @app.post('/upload/preview/<identifier>')
    def preview_import(identifier):
        try:
            header = request.form.get('header_row', '0')
            settings = {'sheet': request.form.get('sheet') or None, 'header_row': None if header == 'none' else int(header),
                        'encoding': request.form.get('encoding', 'auto'), 'delimiter': request.form.get('delimiter', 'auto')}
            if request.form.get('stage') != 'columns':
                settings['mapping'] = {field: int(request.form[field]) for field in ('name', 'ph', 'address')}
        except (KeyError, ValueError):
            raise APIClientError('이름·전화번호·주소 열을 모두 지정해 주세요.', 422)
        result = central('POST', f'/customer-imports/{identifier}/preview', json=settings)['data']
        return render_template('customer_import.html', info=result, preview=result if 'version' in result else None, settings=settings)

    @app.post('/upload/apply/<identifier>')
    def apply_import(identifier):
        result = central('POST', f'/customer-imports/{identifier}/apply', json={
            'version': request.form.get('version'), 'confirm_add': True})['data']
        flash(f"새 고객 {result['new']}명을 추가했습니다. 기존 고객과 중복된 {result['kept']}명은 건너뛰었습니다.")
        return redirect(url_for('view_customer'))

    @app.post('/upload/cancel/<identifier>')
    def cancel_import(identifier):
        central('DELETE', f'/customer-imports/{identifier}')
        return redirect(url_for('mypage'))

    return app


app = create_app()

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False)
