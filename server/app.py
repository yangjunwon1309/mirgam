"""Central authenticated API. Only this process reads or writes SQLite."""
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import secrets
import threading
import time

from flask import Flask, Response, g, jsonify, request
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash

from .common import (APIError, CUSTOMER_COLUMNS, ORDER_COLUMNS, csv_bytes, digest,
                     order_day, payload_hash, positive_quantity, record)
from .db import Database
from .importers import describe, mapped_rows
from .models import Account, AuthSession, Customer, CustomerImport, Order, OrderBatch, Product, now


def create_app(data_dir=None, testing=False, database_url=None):
    app = Flask(__name__, static_folder=None)
    app.config.update(TESTING=testing, MAX_CONTENT_LENGTH=10 * 1024 * 1024)
    database = Database(
        data_dir or os.environ.get('MIRGAM_SERVER_DATA_DIR', 'var/server'),
        database_url=database_url or os.environ.get('MIRGAM_DATABASE_URL'),
    )
    app.extensions['database'] = database
    attempts, attempts_lock = defaultdict(deque), threading.Lock()

    def data(value, **metadata):
        return jsonify(data=value, **metadata)

    def body():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise APIError('입력 내용을 확인해 주세요.')
        return value

    def fields(values, columns):
        if any(not isinstance(values.get(column), str) for column in columns):
            raise APIError('이름, 전화번호, 주소를 모두 입력해 주세요.')
        result = {column: values[column].strip() for column in columns}
        if any(not value or len(value) > 1000 for value in result.values()):
            raise APIError('필수 입력값 또는 길이를 확인해 주세요.')
        return result

    def owned(session, model, identifier):
        obj = session.get(model, identifier)
        if obj is None or obj.account_id != g.account_id:
            raise APIError('대상을 찾을 수 없습니다.', 404, 'not_found')
        return obj

    def customer_record(customer, quantity=0):
        return dict(record(customer, CUSTOMER_COLUMNS), id=customer.id,
                    region=customer.address.strip().split(' ')[0], order_quantity=quantity)

    def order_record(order):
        return dict(record(order, ORDER_COLUMNS), id=order.id, customer_id=order.customer_id,
                    product_id=order.product_id)

    def page_args():
        try:
            page = max(1, int(request.args.get('page', 1)))
            size = max(1, min(200, int(request.args.get('page_size', 50))))
        except ValueError:
            raise APIError('페이지 번호를 확인해 주세요.')
        return page, size

    def order_query():
        query = select(Order).where(Order.account_id == g.account_id, Order.is_deleted == False)
        day, keyword = request.args.get('date', ''), request.args.get('q', '').strip()
        if day:
            if order_day(day) != day:
                raise APIError('주문 날짜를 확인해 주세요.')
            query = query.where(Order.order_day == day)
        if keyword:
            query = query.where(func.lower(Order.name).contains(keyword.lower(), autoescape=True))
        return query.order_by(Order.date.desc(), Order.id.desc())

    def export(rows, columns, filename):
        return Response(csv_bytes(rows, columns), content_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="{filename}"', 'Cache-Control': 'no-store'})

    @app.errorhandler(APIError)
    def validation_error(error):
        return jsonify(error={'code': error.code, 'message': error.message, 'details': error.details}), error.status

    @app.errorhandler(OperationalError)
    def database_error(_):
        return jsonify(error={'code': 'database_busy', 'message': '저장소가 사용 중입니다. 잠시 후 다시 시도해 주세요.'}), 503

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error={'code': 'http_error', 'message': '요청 형식 또는 파일 크기를 확인해 주세요.'}), error.code

    @app.errorhandler(Exception)
    def internal_error(error):
        # No body, token, customer values or exception strings in logs.
        app.logger.error('API failure: %s', type(error).__name__)
        return jsonify(error={'code': 'server_error', 'message': '서버에서 요청을 처리하지 못했습니다.'}), 500

    @app.before_request
    def authenticate():
        if request.path in ('/health', '/api/v1/auth/login'):
            return
        token = request.headers.get('Authorization', '')
        if not token.startswith('Bearer '):
            raise APIError('로그인이 필요합니다.', 401, 'unauthorized')
        with database.session() as session:
            login = session.get(AuthSession, digest(token[7:].encode()))
            account = session.get(Account, login.account_id) if login and login.expires_at > now() else None
            if account is None or not account.is_active:
                raise APIError('로그인이 만료되었습니다. 다시 로그인해 주세요.', 401, 'unauthorized')
            g.account_id, g.account_name = account.id, account.name
            g.token_hash = login.token_hash

    @app.after_request
    def private_response(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.get('/health')
    def health():
        with database.session() as session:
            session.execute(text('SELECT 1'))
        return data({'api_version': 1, 'ready': True})

    @app.post('/api/v1/auth/login')
    def login():
        values = body()
        name, password = values.get('name', ''), values.get('password', '')
        if not isinstance(name, str) or not isinstance(password, str) or len(name) > 1000 or len(password) > 1000:
            raise APIError('로그인 정보를 확인해 주세요.')
        key = (request.remote_addr, digest(name.encode()))
        with attempts_lock:
            history = attempts[key]
            while history and history[0] < time.monotonic() - 60:
                history.popleft()
            if len(history) >= 10:
                raise APIError('로그인 시도가 많습니다. 1분 후 다시 시도해 주세요.', 429, 'rate_limited')
            history.append(time.monotonic())
        with database.session(write=True) as session:
            account = session.scalar(select(Account).where(Account.name == name, Account.is_active == True))
            if account is None or not check_password_hash(account.password_hash, password):
                raise APIError('농장 이름 또는 비밀번호가 틀렸습니다.', 401, 'invalid_login')
            token = secrets.token_urlsafe(32)
            expiry = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
            session.add(AuthSession(token_hash=digest(token.encode()), account_id=account.id, expires_at=expiry))
            result = {'token': token, 'expires_at': expiry, 'account': {'id': account.id, 'name': account.name}}
        with attempts_lock:
            attempts.pop(key, None)
        return data(result)

    @app.get('/api/v1/auth/me')
    def me():
        return data({'id': g.account_id, 'name': g.account_name})

    @app.post('/api/v1/auth/logout')
    def logout():
        with database.session(write=True) as session:
            session.delete(session.get(AuthSession, g.token_hash))
        return data({'logged_out': True})

    @app.get('/api/v1/customers')
    def customers():
        keyword, sort = request.args.get('q', '').strip(), request.args.get('sort', 'name')
        page, size = page_args()
        with database.session() as session:
            query = select(Customer).where(Customer.account_id == g.account_id, Customer.is_active == True)
            if keyword:
                query = query.where(func.lower(Customer.name).contains(keyword.lower(), autoescape=True))
            rows = list(session.scalars(query))
            quantities = defaultdict(Decimal)
            for order in session.scalars(select(Order).where(Order.account_id == g.account_id, Order.is_deleted == False)):
                try:
                    quantity = Decimal(order.quantity)
                    if quantity.is_finite():
                        quantities[(order.name, order.ph, order.address)] += quantity
                except InvalidOperation:
                    pass
            result = [customer_record(row, float(quantities[(row.name, row.ph, row.address)])) for row in rows]
            if sort in ('quantity', 'product'):
                result.sort(key=lambda row: (-row['order_quantity'], row['name'], row['id']))
            else:
                field = 'region' if sort == 'region' else 'name'
                result.sort(key=lambda row: (not bool(row[field]), row[field], row['name'], row['id']))
        return data(result[(page - 1) * size:page * size], total=len(result), page=page, page_size=size)

    @app.post('/api/v1/customers')
    def register():
        values = fields(body(), CUSTOMER_COLUMNS)
        with database.session(write=True) as session:
            customer = session.scalar(select(Customer).where(Customer.account_id == g.account_id,
                                      *[getattr(Customer, field) == value for field, value in values.items()]))
            created = customer is None or not customer.is_active
            if customer is None:
                customer = Customer(account_id=g.account_id, **values)
                session.add(customer)
            else:
                customer.is_active = True
                customer.updated_at = now()
            if created:
                session.get(Account, g.account_id).customer_revision += 1
            session.flush()
            result = {'customer': customer_record(customer), 'created': created}
        return data(result), 201 if created else 200

    @app.patch('/api/v1/customers/<int:identifier>')
    def edit_customer(identifier):
        values = fields(body(), CUSTOMER_COLUMNS)
        with database.session(write=True) as session:
            customer = owned(session, Customer, identifier)
            duplicate = session.scalar(select(Customer).where(Customer.account_id == g.account_id,
                                       Customer.id != identifier,
                                       *[getattr(Customer, field) == value for field, value in values.items()]))
            if duplicate:
                raise APIError('같은 이름·전화번호·주소의 고객이 이미 있습니다.', 409, 'duplicate_customer')
            for field, value in values.items():
                setattr(customer, field, value)
            customer.updated_at = now()
            session.get(Account, g.account_id).customer_revision += 1
            result = customer_record(customer)
        return data(result)

    @app.delete('/api/v1/customers/<int:identifier>')
    def delete_customer(identifier):
        with database.session(write=True) as session:
            customer = owned(session, Customer, identifier)
            if customer.is_active:
                customer.is_active = False
                session.get(Account, g.account_id).customer_revision += 1
        return data({'deleted': identifier})

    @app.get('/api/v1/customers/export.csv')
    def export_customers():
        with database.session() as session:
            rows = [record(row, CUSTOMER_COLUMNS) for row in session.scalars(select(Customer).where(
                Customer.account_id == g.account_id, Customer.is_active == True).order_by(Customer.id))]
        return export(rows, CUSTOMER_COLUMNS, 'customers.csv')

    @app.get('/api/v1/products')
    def products():
        with database.session() as session:
            rows = [dict(record(row, ['item', 'price']), id=row.id) for row in session.scalars(
                select(Product).where(Product.is_active == True).order_by(Product.id))]
        return data(rows)

    @app.post('/api/v1/products')
    def register_product():
        values = body()
        item = fields(values, ['item'])['item']
        price = values.get('price', '')
        if not isinstance(price, str) or (price and (not price.isdigit() or len(price) > 12)):
            raise APIError('가격은 0 이상의 정수로 입력해 주세요.')
        with database.session(write=True) as session:
            product = session.scalar(select(Product).where(Product.item == item))
            if product and product.is_active:
                raise APIError('이미 등록된 상품입니다.', 409, 'duplicate_product')
            if product:
                product.is_active, product.price, product.updated_at = True, price, now()
            else:
                product = Product(item=item, price=price)
                session.add(product)
            session.flush()
            result = dict(record(product, ['item', 'price']), id=product.id)
        return data(result), 201

    @app.patch('/api/v1/products/<int:identifier>')
    def edit_product(identifier):
        values = body()
        price = values.get('price', '')
        if not isinstance(price, str) or (price and (not price.isdigit() or len(price) > 12)):
            raise APIError('가격은 0 이상의 정수로 입력해 주세요.')
        with database.session(write=True) as session:
            product = session.get(Product, identifier)
            if product is None or not product.is_active:
                raise APIError('상품을 찾을 수 없습니다.', 404, 'not_found')
            product.price, product.updated_at = price, now()
        return data({'updated': identifier})

    @app.delete('/api/v1/products/<int:identifier>')
    def delete_product(identifier):
        with database.session(write=True) as session:
            product = session.get(Product, identifier)
            if product is None:
                raise APIError('상품을 찾을 수 없습니다.', 404, 'not_found')
            product.is_active = False
        return data({'deleted': identifier})

    def batch_result(session, batch):
        rows = [order_record(order) for order in session.scalars(select(Order).where(Order.batch_id == batch.id).order_by(Order.id))]
        return {'id': batch.id, 'request_id': batch.request_id, 'count': len(rows), 'orders': rows}

    @app.post('/api/v1/order-batches')
    def create_batch():
        values = body()
        request_id, submitted = values.get('request_id'), values.get('orders')
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise APIError('주문 저장 요청 번호가 없습니다.')
        if not isinstance(submitted, list) or not 1 <= len(submitted) <= 1000:
            raise APIError('저장할 주문을 1~1000건 입력해 주세요.')
        signature = payload_hash(submitted)
        with database.session(write=True) as session:
            existing = session.scalar(select(OrderBatch).where(OrderBatch.account_id == g.account_id,
                                                               OrderBatch.request_id == request_id))
            if existing:
                if existing.payload_hash != signature:
                    raise APIError('동일 요청 번호의 주문 내용이 달라졌습니다.', 409, 'request_conflict')
                return data(batch_result(session, existing))
            rows, errors = [], []
            for index, row in enumerate(submitted, 1):
                if not isinstance(row, dict):
                    errors.append({'row': index, 'message': '주문 형식 오류'})
                    continue
                customer = session.get(Customer, row.get('customer_id')) if type(row.get('customer_id')) is int else None
                product = session.get(Product, row.get('product_id')) if type(row.get('product_id')) is int else None
                quantity, day = row.get('quantity'), row.get('date')
                senders = {field: row.get(field, '') for field in ORDER_COLUMNS[6:]}
                if (customer is None or customer.account_id != g.account_id or not customer.is_active or
                    not all(record(customer, CUSTOMER_COLUMNS).values()) or product is None or not product.is_active or
                    not isinstance(quantity, str) or len(quantity) > 32 or positive_quantity(quantity) is None or
                    not isinstance(day, str) or order_day(day) != day or
                    any(not isinstance(value, str) or len(value) > 1000 for value in senders.values())):
                    errors.append({'row': index, 'message': '고객·상품·양수 수량·유효 주문일·보내는 사람을 확인해 주세요.'})
                    continue
                rows.append(dict(record(customer, CUSTOMER_COLUMNS), item=product.item, quantity=quantity, date=day,
                                 **senders, order_day=day, customer_id=customer.id, product_id=product.id))
            if errors:
                raise APIError('주문을 저장하지 않았습니다. 입력 내용을 확인해 주세요.', details=errors)
            batch = OrderBatch(id=secrets.token_hex(16), account_id=g.account_id, request_id=request_id, payload_hash=signature)
            session.add(batch)
            session.flush()
            for row in rows:
                session.add(Order(account_id=g.account_id, batch_id=batch.id, **row))
            session.flush()
            result = batch_result(session, batch)
        return data(result), 201

    @app.get('/api/v1/order-batches/by-request/<request_id>')
    def get_batch(request_id):
        with database.session() as session:
            batch = session.scalar(select(OrderBatch).where(OrderBatch.account_id == g.account_id,
                                                           OrderBatch.request_id == request_id))
            if batch is None:
                raise APIError('아직 저장되지 않은 주문입니다.', 404, 'not_found')
            result = batch_result(session, batch)
        return data(result)

    @app.get('/api/v1/order-batches/<identifier>/export.csv')
    def export_batch(identifier):
        with database.session() as session:
            batch = owned(session, OrderBatch, identifier)
            result = batch_result(session, batch)
        return export(result['orders'], ORDER_COLUMNS, 'orders.csv')

    @app.get('/api/v1/orders')
    def orders():
        page, size = page_args()
        with database.session() as session:
            query = order_query()
            total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
            rows = [order_record(order) for order in session.scalars(query.offset((page - 1) * size).limit(size))]
        return data(rows, total=total, page=page, page_size=size)

    @app.get('/api/v1/orders/dates')
    def order_dates():
        with database.session() as session:
            rows = session.execute(select(Order.order_day, func.count()).where(Order.account_id == g.account_id,
                Order.is_deleted == False, Order.order_day != None).group_by(Order.order_day).order_by(Order.order_day.desc()))
            result = [{'date': day, 'count': count} for day, count in rows]
        return data(result)

    @app.get('/api/v1/orders/export.csv')
    def export_orders():
        with database.session() as session:
            rows = [order_record(order) for order in session.scalars(order_query())]
        return export(rows, ORDER_COLUMNS, 'orders.csv')

    @app.delete('/api/v1/orders/<int:identifier>')
    def delete_order(identifier):
        with database.session(write=True) as session:
            owned(session, Order, identifier).is_deleted = True
        return data({'deleted': identifier})

    @app.post('/api/v1/customer-imports')
    def upload():
        file = request.files.get('file')
        if file is None:
            raise APIError('고객 파일을 선택해 주세요.')
        suffix = Path(file.filename or '').suffix.lower()
        if suffix not in ('.csv', '.xlsx'):
            raise APIError('.xlsx 또는 .csv만 지원합니다.')
        identifier = secrets.token_hex(16)
        directory = database.directory / 'uploads'
        directory.mkdir(exist_ok=True)
        path = directory / (identifier + suffix)
        file.save(path)
        try:
            settings = {'encoding': 'utf-8-sig', 'delimiter': ','}
            if suffix == '.csv':
                try:
                    path.read_text(encoding='utf-8-sig')
                except UnicodeDecodeError:
                    settings['encoding'] = 'cp949'
            info = describe(path, settings)
            expiry = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
            with database.session(write=True) as session:
                session.add(CustomerImport(id=identifier, account_id=g.account_id, filename=Path(file.filename).name,
                    file_hash=digest(path.read_bytes()), source_path=str(path), expires_at=expiry))
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return data(dict(info, id=identifier)), 201

    def import_job(session, identifier):
        job = owned(session, CustomerImport, identifier)
        if job.expires_at <= now() or job.status == 'cancelled':
            raise APIError('파일 확인 시간이 지났습니다. 다시 파일을 선택해 주세요.', 409, 'expired')
        return job

    @app.post('/api/v1/customer-imports/<identifier>/preview')
    def preview(identifier):
        settings = body()
        with database.session() as session:
            job = import_job(session, identifier)
            if job.status == 'applied':
                raise APIError('이미 반영된 파일입니다.', 409)
            path = job.source_path
        info = describe(path, settings)
        # Allow sheet/header changes before mapping the three columns.
        if 'mapping' not in settings:
            return data(dict(info, id=identifier))
        rows, errors, duplicates = mapped_rows(path, settings)
        with database.session(write=True) as session:
            job = import_job(session, identifier)
            if job.status == 'applied':
                raise APIError('이미 반영된 파일입니다.', 409)
            customers = list(session.scalars(select(Customer).where(Customer.account_id == g.account_id)))
            by_key = {tuple(record(customer, CUSTOMER_COLUMNS).values()): customer for customer in customers}
            keys = {tuple(row[field] for field in CUSTOMER_COLUMNS) for row in rows}
            counts = {'new': sum(key not in by_key for key in keys),
                      'kept': sum(key in by_key and by_key[key].is_active for key in keys),
                      'reactivated': sum(key in by_key and not by_key[key].is_active for key in keys),
                      'duplicates': duplicates, 'valid': len(rows), 'errors': len(errors)}
            version = secrets.token_hex(16)
            result = dict(info, id=identifier, version=version, counts=counts, rows=rows[:50], errors=errors[:100],
                          can_apply=bool(rows) and not errors)
            job.preview_version = version
            job.revision = session.get(Account, g.account_id).customer_revision
            job.preview_json = json.dumps({'rows': rows, 'response': result}, ensure_ascii=False)
            job.status = 'previewed'
        return data(result)

    @app.post('/api/v1/customer-imports/<identifier>/apply')
    def apply_import(identifier):
        values = body()
        with database.session() as session:
            job = import_job(session, identifier)
            if job.status == 'applied' and values.get('version') == job.preview_version:
                return data(json.loads(job.result_json))
        database.backup('before-customer-add')
        with database.session(write=True) as session:
            job = import_job(session, identifier)
            if job.status == 'applied' and values.get('version') == job.preview_version:
                return data(json.loads(job.result_json))
            if job.status != 'previewed' or values.get('version') != job.preview_version or values.get('confirm_add') is not True:
                raise APIError('미리보기 확인 후 새 고객 추가를 승인해 주세요.', 409, 'preview_required')
            preview_value = json.loads(job.preview_json)
            if not preview_value['response']['can_apply']:
                raise APIError('오류가 있는 파일은 반영할 수 없습니다.')
            account = session.get(Account, g.account_id)
            if account.customer_revision != job.revision:
                raise APIError('다른 PC에서 고객 목록이 변경되었습니다. 미리보기를 다시 확인해 주세요.', 409, 'revision_conflict')
            customers = list(session.scalars(select(Customer).where(Customer.account_id == g.account_id)))
            by_key = {tuple(record(customer, CUSTOMER_COLUMNS).values()): customer for customer in customers}
            for row in preview_value['rows']:
                key = tuple(row[field] for field in CUSTOMER_COLUMNS)
                if key in by_key:
                    by_key[key].is_active = True
                    by_key[key].updated_at = now()
                else:
                    session.add(Customer(account_id=g.account_id, **row))
            account.customer_revision += 1
            result = preview_value['response']['counts']
            job.status, job.result_json = 'applied', json.dumps(result)
        try:
            Path(job.source_path).unlink(missing_ok=True)
        except OSError:
            app.logger.warning('Applied import temporary file cleanup deferred')
        return data(result)

    @app.delete('/api/v1/customer-imports/<identifier>')
    def cancel_import(identifier):
        with database.session(write=True) as session:
            job = owned(session, CustomerImport, identifier)
            if job.status == 'applied':
                raise APIError('이미 반영된 파일은 취소할 수 없습니다.', 409)
            job.status = 'cancelled'
            path = job.source_path
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            app.logger.warning('Cancelled import temporary file cleanup deferred')
        return data({'cancelled': True})

    return app
