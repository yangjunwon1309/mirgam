"""Central authenticated API. Only this process reads or writes SQLite."""
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time
from zoneinfo import ZoneInfo

from flask import Flask, Response, g, jsonify, request
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import OperationalError
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash

from .common import (APIError, CUSTOMER_COLUMNS, ORDER_COLUMNS, csv_bytes, digest,
                     order_day, payload_hash, positive_quantity, record)
from .db import Database
from .importers import describe, mapped_rows
from .models import (Account, AuthSession, Customer, CustomerImport, Order, OrderBatch, Product,
                     SmsGateConnection, SmsInbound, SmsOutbox, SmsWebhookEvent, now)
from .sms_gate import (SMS_GATE_EVENTS, SmsGateError, api_call, decrypt_secret, device_identifier,
                       encrypt_secret, json_object, maybe_order_message, message_identifier,
                       normalize_base_url, parse_event_body, phone_digits, response_devices,
                       verify_webhook)


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

    def require_sms_schema():
        if getattr(database, 'schema_version', None) != '0002':
            raise APIError('문자 연동 DB 업그레이드가 필요합니다. 운영자에게 문의해 주세요.', 503, 'schema_upgrade_required')

    def sms_connection_summary(connection):
        if connection is None:
            return {'connected': False}
        return {'connected': bool(connection.enabled), 'base_url': connection.base_url,
                'username': connection.username, 'device_id': connection.device_id,
                'phone_number': connection.phone_number, 'sim_number': connection.sim_number,
                'auto_ack_enabled': bool(connection.auto_ack_enabled),
                'auto_ack_text': connection.auto_ack_text, 'updated_at': connection.updated_at}

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
        if (request.path in ('/health', '/api/v1/auth/login') or
                request.path.startswith('/api/v1/sms-gate/webhooks/')):
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

    @app.get('/api/v1/sms-gate/connection')
    def get_sms_connection():
        if getattr(database, 'schema_version', None) != '0002':
            return data({'connected': False, 'schema_ready': False})
        with database.session() as session:
            connection = session.scalar(select(SmsGateConnection).where(SmsGateConnection.account_id == g.account_id))
            result = sms_connection_summary(connection)
            result['schema_ready'] = True
        return data(result)

    @app.put('/api/v1/sms-gate/connection')
    def save_sms_connection():
        require_sms_schema()
        values = body()
        try:
            base_url = normalize_base_url(values.get('base_url', ''))
        except SmsGateError as error:
            raise APIError(str(error), 422, 'invalid_sms_gateway_url') from error
        username, device_id = values.get('username'), values.get('device_id')
        phone_number = values.get('phone_number')
        try:
            sim_number = int(values.get('sim_number', 1))
        except (TypeError, ValueError):
            sim_number = 0
        if (not isinstance(username, str) or not 1 <= len(username.strip()) <= 200 or
                not isinstance(device_id, str) or not 3 <= len(device_id.strip()) <= 128 or
                not isinstance(phone_number, str) or not 7 <= len(phone_digits(phone_number)) <= 15 or
                sim_number not in (1, 2, 3)):
            raise APIError('SMS Gateway 계정, 기기 ID, 휴대폰 번호, SIM 번호를 확인해 주세요.')
        username, device_id, phone_number = username.strip(), device_id.strip(), phone_number.strip()
        password_input, signing_input = values.get('password', ''), values.get('signing_key', '')
        if not isinstance(password_input, str) or not isinstance(signing_input, str):
            raise APIError('SMS Gateway 인증 정보를 확인해 주세요.')
        with database.session() as session:
            existing = session.scalar(select(SmsGateConnection).where(SmsGateConnection.account_id == g.account_id))
            owner = session.scalar(select(SmsGateConnection).where(SmsGateConnection.device_id == device_id,
                                                                   SmsGateConnection.account_id != g.account_id,
                                                                   SmsGateConnection.enabled == True))
            if owner:
                raise APIError('이 기기는 이미 다른 농장 계정에 연결되어 있습니다.', 409, 'device_in_use')
            reusable = bool(existing and existing.enabled and existing.device_id == device_id and
                            existing.username == username and existing.base_url == base_url)
            try:
                password = password_input or (decrypt_secret(existing.password_ciphertext) if reusable else '')
                signing_key = signing_input or (decrypt_secret(existing.signing_key_ciphertext) if reusable else '')
                encrypted_password = encrypt_secret(password) if password else ''
                encrypted_signing_key = encrypt_secret(signing_key) if signing_key else ''
            except SmsGateError as error:
                raise APIError(str(error), 503, 'sms_secret_config') from error
            old_connection = None
            if existing and existing.enabled:
                old_connection = {'base_url': existing.base_url, 'username': existing.username,
                                  'password': decrypt_secret(existing.password_ciphertext),
                                  'webhook_ids': json_object(existing.webhook_ids_json)}
        if not password or not signing_key:
            raise APIError('SMS Gateway 비밀번호와 앱의 Webhooks Signing Key를 입력해 주세요.', 422, 'sms_credentials_required')

        try:
            devices = response_devices(api_call(base_url, username, password, 'GET', '/devices'))
        except SmsGateError as error:
            raise APIError(str(error), 502, 'sms_gateway_unavailable') from error
        if not any(device_identifier(device) == device_id for device in devices):
            raise APIError('입력한 기기 ID를 SMS Gateway 계정에서 찾지 못했습니다.', 422, 'sms_device_not_found')

        public_base = os.environ.get('MIRGAM_PUBLIC_BASE_URL', '').strip().rstrip('/')
        if not public_base.startswith('https://'):
            raise APIError('서버에 MIRGAM_PUBLIC_BASE_URL HTTPS 주소를 설정해야 웹훅을 등록할 수 있습니다.', 503, 'webhook_url_missing')
        webhook_token = secrets.token_urlsafe(32)
        webhook_url = f'{public_base}/api/v1/sms-gate/webhooks/{webhook_token}'
        registered = {}
        try:
            for event_type in SMS_GATE_EVENTS:
                response = api_call(base_url, username, password, 'POST', '/webhooks', json_body={
                    'url': webhook_url, 'event': event_type, 'device_id': device_id})
                hook_id = message_identifier(response)
                if not hook_id:
                    raise SmsGateError('SMS Gateway가 웹훅 ID를 반환하지 않았습니다. 등록 목록을 확인해 주세요.')
                registered[event_type] = hook_id
        except SmsGateError as error:
            for hook_id in registered.values():
                try:
                    api_call(base_url, username, password, 'DELETE', '/webhooks/' + hook_id)
                except SmsGateError:
                    pass
            raise APIError(str(error), 502, 'webhook_registration_failed') from error

        auto_ack_value = values.get('auto_ack_enabled', False)
        auto_ack_enabled = auto_ack_value is True or str(auto_ack_value).lower() in ('1', 'true', 'yes', 'on')
        auto_ack_text = values.get('auto_ack_text', '미르감: 주문이 접수되었습니다.')
        if not isinstance(auto_ack_text, str) or not auto_ack_text.strip() or len(auto_ack_text) > 500:
            raise APIError('주문 접수 문자 문구를 1~500자로 입력해 주세요.')
        stamp = now()
        try:
            with database.session(write=True) as session:
                connection = session.scalar(select(SmsGateConnection).where(SmsGateConnection.account_id == g.account_id))
                if connection is None:
                    connection = SmsGateConnection(account_id=g.account_id, base_url=base_url, username=username,
                        password_ciphertext=encrypted_password, signing_key_ciphertext=encrypted_signing_key,
                        device_id=device_id, phone_number=phone_number, sim_number=sim_number,
                        webhook_token=webhook_token, webhook_ids_json=json.dumps(registered),
                        auto_ack_enabled=auto_ack_enabled, auto_ack_text=auto_ack_text.strip(),
                        enabled=True, created_at=stamp, updated_at=stamp)
                    session.add(connection)
                else:
                    connection.base_url, connection.username = base_url, username
                    connection.password_ciphertext, connection.signing_key_ciphertext = encrypted_password, encrypted_signing_key
                    connection.device_id, connection.phone_number, connection.sim_number = device_id, phone_number, sim_number
                    connection.webhook_token, connection.webhook_ids_json = webhook_token, json.dumps(registered)
                    connection.auto_ack_enabled, connection.auto_ack_text = auto_ack_enabled, auto_ack_text.strip()
                    connection.enabled, connection.updated_at = True, stamp
                session.flush()
                result = sms_connection_summary(connection)
        except IntegrityError as error:
            for hook_id in registered.values():
                try:
                    api_call(base_url, username, password, 'DELETE', '/webhooks/' + hook_id)
                except SmsGateError:
                    pass
            raise APIError('기기 연결이 동시에 변경되었습니다. 화면을 새로고침하고 다시 시도해 주세요.', 409, 'sms_connection_conflict') from error

        if old_connection:
            for hook_id in old_connection['webhook_ids'].values():
                try:
                    api_call(old_connection['base_url'], old_connection['username'], old_connection['password'],
                             'DELETE', '/webhooks/' + str(hook_id))
                except SmsGateError:
                    pass
        return data(result)

    @app.delete('/api/v1/sms-gate/connection')
    def delete_sms_connection():
        require_sms_schema()
        old = None
        with database.session(write=True) as session:
            connection = session.scalar(select(SmsGateConnection).where(SmsGateConnection.account_id == g.account_id))
            if connection and connection.enabled:
                try:
                    old = {'base_url': connection.base_url, 'username': connection.username,
                           'password': decrypt_secret(connection.password_ciphertext),
                           'webhook_ids': json_object(connection.webhook_ids_json)}
                except SmsGateError:
                    old = None
                for pending in session.scalars(select(SmsOutbox).where(SmsOutbox.connection_id == connection.id,
                                                                        SmsOutbox.state == 'pending')):
                    pending.state, pending.last_error_code, pending.updated_at = 'cancelled', 'connection_removed', now()
                connection.enabled = False
                connection.username = ''
                connection.password_ciphertext = ''
                connection.signing_key_ciphertext = ''
                connection.device_id = f'disconnected-{connection.id}-{secrets.token_hex(8)}'
                connection.webhook_ids_json = '{}'
                connection.auto_ack_enabled = False
                connection.webhook_token = secrets.token_urlsafe(32)
                connection.updated_at = now()
        if old:
            for hook_id in old['webhook_ids'].values():
                try:
                    api_call(old['base_url'], old['username'], old['password'], 'DELETE', '/webhooks/' + str(hook_id))
                except SmsGateError:
                    pass
        return data({'disconnected': True})

    @app.post('/api/v1/sms-gate/webhooks/<token>')
    def sms_gate_webhook(token):
        require_sms_schema()
        with database.session() as session:
            connection = session.scalar(select(SmsGateConnection).where(SmsGateConnection.webhook_token == token,
                                                                          SmsGateConnection.enabled == True))
            if connection is None:
                raise APIError('웹훅 연결을 찾을 수 없습니다.', 404, 'webhook_not_found')
            try:
                signing_key = decrypt_secret(connection.signing_key_ciphertext)
            except SmsGateError as error:
                raise APIError('웹훅 서명을 확인할 수 없습니다.', 503, 'webhook_key_unavailable') from error
            connection_id, account_id = connection.id, connection.account_id
            allowed_webhooks = set(json_object(connection.webhook_ids_json).values())
            expected_device = connection.device_id
        raw_body = request.get_data(cache=True)
        timestamp, signature = request.headers.get('X-Timestamp', ''), request.headers.get('X-Signature', '')
        if not verify_webhook(signing_key, raw_body, timestamp, signature, int(time.time())):
            raise APIError('웹훅 서명이 유효하지 않습니다.', 401, 'invalid_webhook_signature')
        event = request.get_json(silent=True)
        if not isinstance(event, dict) or event.get('deviceId') != expected_device:
            raise APIError('웹훅 기기 정보가 일치하지 않습니다.', 403, 'webhook_device_mismatch')
        event_type = event.get('event')
        provider_event_id = str(event.get('id') or '')
        webhook_id = str(event.get('webhookId') or '')
        payload = parse_event_body(event)
        if (event_type not in SMS_GATE_EVENTS or not provider_event_id or len(provider_event_id) > 160 or
                not payload or (allowed_webhooks and webhook_id not in allowed_webhooks)):
            raise APIError('웹훅 내용이 올바르지 않습니다.', 422, 'invalid_webhook_payload')

        stamp = now()
        if event_type == 'sms:received':
            message_text = payload.get('message')
            sender_phone = str(payload.get('sender') or payload.get('phoneNumber') or '').strip()
            if not isinstance(message_text, str) or not sender_phone:
                return data({'accepted': True, 'ignored': True})
            with database.session() as session:
                connection = session.get(SmsGateConnection, connection_id)
                products = list(session.scalars(select(Product).where(Product.is_active == True)))
                matched_product, quantity_hint = maybe_order_message(message_text, products)
                if matched_product is None and not any(word in message_text.casefold() for word in ('[주문]', '주문', '주문해', '보내주세요', '보내 줘', '부탁해')):
                    return data({'accepted': True, 'ignored': True})
                existing_event = session.scalar(select(SmsWebhookEvent).where(
                    SmsWebhookEvent.connection_id == connection_id,
                    SmsWebhookEvent.provider_event_id == provider_event_id))
                if existing_event:
                    return data({'accepted': True, 'duplicate': True})
                candidates = list(session.scalars(select(Customer).where(Customer.account_id == account_id,
                                                                          Customer.is_active == True)))
                phone_match = phone_digits(sender_phone)
                matching_customers = [customer for customer in candidates if phone_digits(customer.ph) == phone_match]
                suggested_customer = matching_customers[0] if len(matching_customers) == 1 else None
                received_raw = payload.get('receivedAt')
                try:
                    received = datetime.fromisoformat(str(received_raw).replace('Z', '+00:00'))
                    if received.tzinfo is None:
                        received = received.replace(tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    received = datetime.now(timezone.utc)
                received = received.astimezone(timezone.utc)
                day = received.astimezone(ZoneInfo('Asia/Seoul')).date().isoformat()
                try:
                    body_ciphertext = encrypt_secret(message_text)
                except SmsGateError as error:
                    raise APIError(str(error), 503, 'sms_secret_config') from error
            with database.session(write=True) as session:
                duplicate = session.scalar(select(SmsWebhookEvent).where(
                    SmsWebhookEvent.connection_id == connection_id,
                    SmsWebhookEvent.provider_event_id == provider_event_id))
                if duplicate:
                    return data({'accepted': True, 'duplicate': True})
                session.add(SmsWebhookEvent(connection_id=connection_id, provider_event_id=provider_event_id,
                                            event_type=event_type, received_at=stamp))
                inbound = SmsInbound(account_id=account_id, connection_id=connection_id,
                    provider_event_id=provider_event_id, provider_message_id=str(payload.get('messageId') or '')[:160],
                    sender_phone=sender_phone, received_at=received.isoformat(), body_ciphertext=body_ciphertext,
                    body_hash=hashlib.sha256(message_text.encode('utf-8')).hexdigest(),
                    suggested_customer_id=suggested_customer.id if suggested_customer else None,
                    suggested_product_id=matched_product.id if matched_product else None,
                    suggested_quantity=quantity_hint, order_day=day, state='review', created_at=stamp)
                session.add(inbound)
            return data({'accepted': True})

        message_id = str(payload.get('messageId') or '')[:160]
        state_by_event = {'sms:sent': 'sent', 'sms:delivered': 'delivered', 'sms:failed': 'failed'}
        with database.session(write=True) as session:
            duplicate = session.scalar(select(SmsWebhookEvent).where(
                SmsWebhookEvent.connection_id == connection_id,
                SmsWebhookEvent.provider_event_id == provider_event_id))
            if duplicate:
                return data({'accepted': True, 'duplicate': True})
            session.add(SmsWebhookEvent(connection_id=connection_id, provider_event_id=provider_event_id,
                                        event_type=event_type, received_at=stamp))
            outbox = session.scalar(select(SmsOutbox).where(SmsOutbox.provider_message_id == message_id)) if message_id else None
            if outbox and event_type in state_by_event:
                outbox.state = state_by_event[event_type]
                outbox.last_error_code = str(payload.get('reason') or '')[:100] if event_type == 'sms:failed' else None
                outbox.updated_at = stamp
        return data({'accepted': True})

    @app.get('/api/v1/sms/inbound')
    def list_sms_inbound():
        require_sms_schema()
        page, size = page_args()
        requested_state = request.args.get('state', 'review')
        if requested_state not in ('review', 'confirmed', 'ignored', 'all'):
            raise APIError('문자 주문 상태를 확인해 주세요.')
        with database.session() as session:
            query = select(SmsInbound).where(SmsInbound.account_id == g.account_id)
            if requested_state != 'all':
                query = query.where(SmsInbound.state == requested_state)
            total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
            rows = list(session.scalars(query.order_by(SmsInbound.id.desc()).offset((page - 1) * size).limit(size)))
            customers = list(session.scalars(select(Customer).where(Customer.account_id == g.account_id,
                                                                       Customer.is_active == True)))
            result = []
            for inbound in rows:
                try:
                    message_text = decrypt_secret(inbound.body_ciphertext)
                except SmsGateError:
                    message_text = '[문자 내용을 복호화할 수 없습니다. 서버 키 설정을 확인해 주세요.]'
                sender_digits = phone_digits(inbound.sender_phone)
                matches = [dict(id=customer.id, name=customer.name, ph=customer.ph, address=customer.address)
                           for customer in customers if phone_digits(customer.ph) == sender_digits]
                result.append({'id': inbound.id, 'sender_phone': inbound.sender_phone,
                    'received_at': inbound.received_at, 'message': message_text, 'state': inbound.state,
                    'suggested_customer_id': inbound.suggested_customer_id,
                    'customer_matches': matches[:50], 'suggested_product_id': inbound.suggested_product_id,
                    'suggested_quantity': inbound.suggested_quantity, 'order_day': inbound.order_day,
                    'order_batch_id': inbound.order_batch_id})
        return data(result, total=total, page=page, page_size=size)

    @app.post('/api/v1/sms/inbound/<int:identifier>/confirm')
    def confirm_sms_order(identifier):
        require_sms_schema()
        values = body()
        customer_id = values.get('customer_id')
        new_customer = values.get('customer')
        product_id = values.get('product_id')
        quantity, day = values.get('quantity'), values.get('date')
        if type(customer_id) is not int and not isinstance(new_customer, dict):
            raise APIError('주문 고객을 선택하거나 새 고객 정보를 입력해 주세요.')
        if type(product_id) is not int or not isinstance(quantity, str) or positive_quantity(quantity) is None:
            raise APIError('상품과 양수 수량을 확인해 주세요.')
        if not isinstance(day, str) or order_day(day) != day:
            raise APIError('주문일을 확인해 주세요.')
        with database.session(write=True) as session:
            inbound = session.scalar(select(SmsInbound).where(SmsInbound.id == identifier,
                                                               SmsInbound.account_id == g.account_id))
            if inbound is None:
                raise APIError('수신 문자를 찾을 수 없습니다.', 404, 'not_found')
            if inbound.state == 'confirmed' and inbound.order_batch_id:
                batch = session.get(OrderBatch, inbound.order_batch_id)
                return data({'confirmed': True, 'duplicate': True,
                             'order_batch_id': batch.id if batch else inbound.order_batch_id})
            if inbound.state != 'review':
                raise APIError('이미 무시했거나 처리된 문자입니다.', 409, 'sms_already_processed')
            if type(customer_id) is int:
                customer = session.scalar(select(Customer).where(Customer.id == customer_id,
                    Customer.account_id == g.account_id, Customer.is_active == True))
                if customer is None:
                    raise APIError('선택한 고객을 찾을 수 없습니다.', 404, 'not_found')
            else:
                customer_values = fields(new_customer, CUSTOMER_COLUMNS)
                customer = session.scalar(select(Customer).where(Customer.account_id == g.account_id,
                    *[getattr(Customer, field) == value for field, value in customer_values.items()]))
                if customer is None:
                    customer = Customer(account_id=g.account_id, **customer_values)
                    session.add(customer)
                    session.flush()
                    session.get(Account, g.account_id).customer_revision += 1
                elif not customer.is_active:
                    customer.is_active = True
                    customer.updated_at = now()
                    session.get(Account, g.account_id).customer_revision += 1
            product = session.get(Product, product_id)
            if product is None or not product.is_active:
                raise APIError('선택한 상품을 찾을 수 없습니다.', 404, 'not_found')
            connection = session.get(SmsGateConnection, inbound.connection_id)
            record_values = dict(record(customer, CUSTOMER_COLUMNS), item=product.item, quantity=quantity,
                                 date=day, sender_name='', sender_ph='', sender_address='',
                                 order_day=day, customer_id=customer.id, product_id=product.id)
            request_id = f'sms-inbound-{inbound.id}'
            existing_batch = session.scalar(select(OrderBatch).where(OrderBatch.account_id == g.account_id,
                                                                        OrderBatch.request_id == request_id))
            if existing_batch:
                batch = existing_batch
            else:
                batch = OrderBatch(id=secrets.token_hex(16), account_id=g.account_id,
                    request_id=request_id, payload_hash=payload_hash([record_values]), created_at=now())
                session.add(batch)
                session.flush()
                session.add(Order(account_id=g.account_id, batch_id=batch.id, **record_values))
                session.flush()
            inbound.state, inbound.order_batch_id, inbound.reviewed_at = 'confirmed', batch.id, now()
            outbox = None
            if connection and connection.enabled and connection.auto_ack_enabled and inbound.sender_phone:
                digits = phone_digits(inbound.sender_phone)
                recipient = ('+82' + digits[1:]) if digits.startswith('0') and len(digits) in (10, 11) else ('+' + digits if 8 <= len(digits) <= 15 else '')
                if recipient:
                    try:
                        ack_text = connection.auto_ack_text.format(name=customer.name, item=product.item, quantity=quantity)
                    except (KeyError, ValueError):
                        ack_text = connection.auto_ack_text
                    try:
                        encrypted_ack = encrypt_secret(ack_text)
                        outbox = SmsOutbox(id=secrets.token_hex(16), account_id=g.account_id,
                            connection_id=connection.id, inbound_id=inbound.id, order_batch_id=batch.id,
                            recipient_phone=recipient, body_ciphertext=encrypted_ack, state='pending',
                            attempt_count=0, created_at=now(), updated_at=now())
                    except SmsGateError:
                        outbox = None
                else:
                    outbox = SmsOutbox(id=secrets.token_hex(16), account_id=g.account_id,
                        connection_id=connection.id, inbound_id=inbound.id, order_batch_id=batch.id,
                        recipient_phone=inbound.sender_phone, body_ciphertext='', state='failed',
                        attempt_count=0, last_error_code='invalid_recipient_phone', created_at=now(), updated_at=now())
                if outbox:
                    session.add(outbox)
            result = {'confirmed': True, 'order_batch_id': batch.id,
                      'ack_queued': bool(connection and connection.enabled and connection.auto_ack_enabled and outbox)}
        return data(result), 201

    @app.post('/api/v1/sms/inbound/<int:identifier>/ignore')
    def ignore_sms_order(identifier):
        require_sms_schema()
        with database.session(write=True) as session:
            inbound = session.scalar(select(SmsInbound).where(SmsInbound.id == identifier,
                                                               SmsInbound.account_id == g.account_id))
            if inbound is None:
                raise APIError('수신 문자를 찾을 수 없습니다.', 404, 'not_found')
            if inbound.state == 'review':
                inbound.state, inbound.reviewed_at = 'ignored', now()
        return data({'ignored': True})

    @app.get('/api/v1/sms/outbox')
    def list_sms_outbox():
        require_sms_schema()
        page, size = page_args()
        with database.session() as session:
            rows = list(session.scalars(select(SmsOutbox).where(SmsOutbox.account_id == g.account_id)
                                        .order_by(SmsOutbox.id.desc()).offset((page - 1) * size).limit(size)))
            result = [{'id': row.id, 'recipient_phone': row.recipient_phone, 'state': row.state,
                       'attempt_count': row.attempt_count, 'last_error_code': row.last_error_code,
                       'created_at': row.created_at, 'updated_at': row.updated_at} for row in rows]
        return data(result)

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
