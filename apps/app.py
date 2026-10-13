"""Local UI gateway. All business reads/writes go through the central API."""
from datetime import datetime
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from zoneinfo import ZoneInfo

from flask import Flask, Response, flash, jsonify, redirect, render_template, request, session, url_for
from .api_client import APIClient, APIClientError, load_client_config
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
    config = load_client_config()
    configured_servers = config.get('servers')
    if api_url:
        configured_api_url = api_url
        configured_servers = [{'id': 'selected', 'name': '중앙 서버',
                               'url': configured_api_url}]
    else:
        if not isinstance(configured_servers, list) or not configured_servers:
            configured_api_url = (os.environ.get('MIRGAM_API_BASE_URL') or
                                  config.get('MIRGAM_API_BASE_URL'))
        else:
            configured_api_url = None
        if configured_api_url:
            configured_servers = [{'id': 'selected', 'name': '중앙 서버',
                                   'url': configured_api_url}]
        elif not isinstance(configured_servers, list) or not configured_servers:
            configured_servers = [{'id': 'central', 'name': '중앙 서버',
                                   'url': config.get('api_base_url', 'http://127.0.0.1:5100')}]
    servers = []
    for entry in configured_servers:
        if not isinstance(entry, dict) or not all(isinstance(entry.get(key), str) for key in ('id', 'name', 'url')):
            continue
        try:
            APIClient(entry['url'], transport)
        except ValueError:
            continue
        servers.append(dict(id=entry['id'], name=entry['name'], url=entry['url']))
    if not servers:
        servers = [{'id': 'central', 'name': '중앙 서버', 'url': 'http://127.0.0.1:5100'}]
    initial_server = next((item for item in servers if item['id'] == config.get('default_server_id')),
                          servers[0])
    api = APIClient(api_url or initial_server['url'], transport)
    app.extensions.update(api_client=api, api_servers=servers, login_states={})
    state_lock = threading.RLock()

    def state():
        value = app.extensions['login_states'].get(session.get('client_id'))
        if value is None:
            raise APIClientError('로그인한 후 이용해 주세요.', 401, code='unauthorized')
        return value

    def central(method, path, **kwargs):
        login_state = state()
        return login_state['api'].request(method, '/api/v1' + path, token=login_state['token'], **kwargs)

    def cached_get(path, params=None, ttl=10):
        login_state = state()
        cache = login_state.setdefault('api_cache', {})
        key = (path, tuple(sorted((params or {}).items())))
        now = time.monotonic()
        with state_lock:
            cached = cache.get(key)
            if cached and cached[0] > now:
                return deepcopy(cached[1])
        result = central('GET', path, params=params or {})
        with state_lock:
            if len(cache) >= 32:
                cache.clear()
            cache[key] = (time.monotonic() + ttl, deepcopy(result))
        return result

    def invalidate_cache(*paths):
        cache = state().setdefault('api_cache', {})
        with state_lock:
            for key in list(cache):
                if any(key[0] == path for path in paths):
                    cache.pop(key, None)

    def login_server_statuses():
        selected_id = config.get('default_server_id')
        statuses = []
        for server in servers:
            available, label = False, '연결 안 됨'
            try:
                APIClient(server['url'], transport).health(timeout=(1, 2))
                available, label = True, '연결 가능'
            except APIClientError:
                pass
            statuses.append({**server, 'available': available, 'status': label,
                             'selected': server['id'] == selected_id})
        if not any(item['selected'] for item in statuses):
            for item in statuses:
                if item['available']:
                    item['selected'] = True
                    break
        return statuses

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
            for old_label, new_label in (
                ('>주문 입력하기</a>', '>주문 입력</a>'),
                ('>주문 내역 보기</a>', '>주문 내역</a>'),
                ('>고객 확인 및 수정</a>', '>고객 관리</a>'),
                ('>농장 설정</a>', '>설정</a>'),
            ):
                html = html.replace(old_label, new_label)
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
                selected_server = next((item for item in servers if item['id'] == request.form.get('server_id')), None)
                if selected_server is None:
                    selected_server = next((item for item in servers if item['id'] == config.get('default_server_id')),
                                           servers[0])
                # Reuse the configured API client when the selected endpoint is
                # the same one. This also preserves test/custom transports.
                selected_api = (api if selected_server['url'].rstrip('/') == api.base_url.rstrip('/')
                                else APIClient(selected_server['url'], transport))
                selected_api.health()
                result = selected_api.request('POST', '/api/v1/auth/login', json={
                    'name': request.form.get('farm-name', ''), 'password': request.form.get('password', '')})['data']
            except APIClientError as error:
                flash(error.message)
                return render_template('login.html', servers=login_server_statuses()), error.status
            app.extensions['login_states'].pop(session.get('client_id'), None)
            session.clear()
            identifier = secrets.token_urlsafe(32)
            session['client_id'] = identifier
            app.extensions['login_states'][identifier] = {'token': result['token'], 'name': result['account']['name'],
                'api': selected_api, 'server_name': selected_server['name'], 'pending': {}, 'items': []}
            return redirect(url_for('order'))
        return render_template('login.html', servers=login_server_statuses())

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
        result = central('GET', '/sms/inbound', params={
            'state': state_filter, 'page': 1, 'page_size': 50})
        messages = result['data']
        products = cached_get('/products', ttl=60)['data'] if any(
            message['state'] == 'review' for message in messages) else []
        return render_template('sms_orders.html', messages=messages, products=products,
                               selected_state=state_filter, total=result['total'], page=1,
                               page_size=50, today=today().isoformat())

    @app.get('/sms-orders/messages')
    def sms_order_messages_fragment():
        state_filter = request.args.get('state', 'review')
        if state_filter not in ('review', 'confirmed', 'ignored', 'all'):
            raise APIClientError('문자 상태를 확인해 주세요.', 422)
        try:
            page = max(1, int(request.args.get('page', '1')))
        except ValueError:
            raise APIClientError('페이지 번호를 확인해 주세요.', 422)
        result = central('GET', '/sms/inbound', params={
            'state': state_filter, 'page': page, 'page_size': 50})
        messages = result['data']
        products = cached_get('/products', ttl=60)['data'] if any(
            message['state'] == 'review' for message in messages) else []
        return render_template('sms_message_list.html', messages=messages, products=products)

    @app.get('/sms-orders/panel/<panel_name>')
    def sms_orders_panel(panel_name):
        if panel_name == 'marketing':
            first = cached_get('/sms/marketing/customers', {'page': 1, 'page_size': 2000}, ttl=15)
            customers = list(first['data'])
            page = 2
            while len(customers) < first['total']:
                next_page = cached_get('/sms/marketing/customers',
                                       {'page': page, 'page_size': 2000}, ttl=15)
                if not next_page['data']:
                    break
                customers.extend(next_page['data'])
                page += 1
            return render_template('sms_marketing_panel.html', marketing_customers=customers,
                                   today=today().isoformat())
        if panel_name == 'campaigns':
            campaigns = cached_get('/sms/campaigns', ttl=5)['data']
            return render_template('sms_campaigns_panel.html', campaigns=campaigns)
        if panel_name == 'outbox':
            try:
                page = max(1, int(request.args.get('page', '1')))
            except ValueError:
                raise APIClientError('페이지 번호를 확인해 주세요.', 422)
            result = cached_get('/sms/outbox', {'page': page, 'page_size': 25}, ttl=5)
            return render_template('sms_outbox_panel.html', outbox=result['data'], total=result['total'],
                                   page=page, fragment=page > 1)
        raise APIClientError('요청한 문자 항목을 찾을 수 없습니다.', 404, code='not_found')

    @app.post('/sms-marketing/consent/all')
    def record_existing_sms_consent():
        result = central('POST', '/sms/marketing/consent/all-unknown', json={
            'confirm_existing_consent': request.form.get('confirm_existing_consent') == 'yes',
            'consent_date': request.form.get('consent_date', '')})['data']
        invalidate_cache('/sms/marketing/customers')
        flash(f"기존 동의 기록 {result['recorded']}명을 등록했습니다. 이미 수신 거부한 고객은 변경하지 않았습니다.")
        return redirect(url_for('sms_orders'))

    @app.post('/sms-marketing/campaigns')
    def create_sms_campaign():
        try:
            customer_ids = [int(value) for value in request.form.getlist('customer_ids')]
        except ValueError:
            raise APIClientError('선택한 고객을 다시 확인해 주세요.', 422)
        result = central('POST', '/sms/campaigns', json={
            'customer_ids': customer_ids, 'message': request.form.get('message', ''),
            'confirm_advertising': request.form.get('confirm_advertising') == 'yes',
            'opt_out_number': request.form.get('opt_out_number', ''),
            'duration_days': request.form.get('duration_days', ''),
            'daily_limit': request.form.get('daily_limit', ''),
            'start_time': request.form.get('start_time', ''), 'end_time': request.form.get('end_time', ''),
        })['data']
        invalidate_cache('/sms/campaigns')
        flash(f"{result['total']}명에게 보낼 문자를 예약했습니다. 발송은 표시된 기간에 나누어 진행됩니다.")
        return redirect(url_for('sms_orders'))

    @app.post('/sms-marketing/campaigns/<identifier>/cancel')
    def cancel_sms_campaign(identifier):
        if not re.fullmatch(r'[a-f0-9]{32}', identifier):
            raise APIClientError('발송 일정을 확인해 주세요.', 422)
        central('DELETE', '/sms/campaigns/' + identifier)
        invalidate_cache('/sms/campaigns')
        flash('아직 보내지 않은 예약 문자를 취소했습니다. 이미 휴대폰으로 전달된 문자는 중지되지 않을 수 있습니다.')
        return redirect(url_for('sms_orders'))

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
        invalidate_cache('/sms/outbox')
        invalidate_cache('/sms/marketing/customers')
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
        invalidate_cache('/sms/marketing/customers')
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
                consent_action = request.form.get('marketing_consent_action', 'keep')
                if consent_action in ('yes', 'no'):
                    central('PATCH', '/sms/marketing/customers/' + identifier, json={
                        'consent': consent_action == 'yes', 'consent_date': request.form.get('marketing_consent_date', '')})
                flash('고객 정보를 수정했습니다. 이전 주문의 배송 정보는 유지됩니다.')
            else:
                central('DELETE', '/customers/' + identifier)
                flash('선택한 고객을 삭제했습니다. 이전 주문은 유지됩니다.')
        else:
            result = central('POST', '/customers', json={'name': request.form.get('new_name'), 'ph': request.form.get('new_phone'), 'address': request.form.get('new_address')})['data']
            flash('고객을 등록했습니다.' if result['created'] else '같은 이름·전화번호·주소의 고객이 이미 있습니다.')
        invalidate_cache('/sms/marketing/customers')
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
            invalidate_cache('/products')
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
        invalidate_cache('/sms/marketing/customers')
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
