"""Single-process worker that sends confirmed-order SMS outbox messages."""
import time

from sqlalchemy import select

from .models import SmsGateConnection, SmsOutbox, now
from .sms_gate import SmsGateError, decrypt_secret, normalize_base_url


def process_one(database, requests_module=None):
    """Claim and send one message. Ambiguous network failures are never retried."""
    if requests_module is None:
        import requests as requests_module

    claimed = None
    with database.session(write=True) as session:
        row = session.scalar(select(SmsOutbox).where(SmsOutbox.state == 'pending')
                             .order_by(SmsOutbox.created_at, SmsOutbox.id).limit(1))
        if row is None:
            return False
        connection = session.scalar(select(SmsGateConnection).where(
            SmsGateConnection.id == row.connection_id,
            SmsGateConnection.account_id == row.account_id,
            SmsGateConnection.enabled == True))
        if connection is None:
            row.state, row.last_error_code, row.updated_at = 'cancelled', 'connection_unavailable', now()
            return True
        try:
            claimed = {
                'id': row.id,
                'url': normalize_base_url(connection.base_url) + '/3rdparty/v1/messages',
                'username': connection.username,
                'password': decrypt_secret(connection.password_ciphertext),
                'device_id': connection.device_id,
                'sim_number': connection.sim_number,
                'recipient': row.recipient_phone,
                'message': decrypt_secret(row.body_ciphertext),
            }
        except SmsGateError:
            row.state, row.last_error_code, row.updated_at = 'failed', 'credentials_unavailable', now()
            return True
        # The custom id is sent to SMS Gateway and may be echoed by a webhook
        # before the POST response is received; store it before making the call.
        row.state, row.attempt_count, row.updated_at = 'sending', row.attempt_count + 1, now()
        row.provider_message_id = row.id

    payload = {
        'id': claimed['id'],
        'deviceId': claimed['device_id'],
        'phoneNumbers': [claimed['recipient']],
        'textMessage': {'text': claimed['message']},
        'simNumber': claimed['sim_number'],
        'ttl': 3600,
    }
    try:
        response = requests_module.post(claimed['url'], auth=(claimed['username'], claimed['password']),
            json=payload, timeout=(5, 25), allow_redirects=False)
    except requests_module.RequestException:
        # The provider might have accepted the request before the connection failed.
        state, error_code, provider_id = 'unknown', 'network_result_unknown', None
    else:
        if 200 <= response.status_code < 300:
            try:
                result = response.json() if response.content else {}
            except ValueError:
                result = {}
            provider_id = str(result.get('id') or claimed['id'])[:160] if isinstance(result, dict) else claimed['id']
            state, error_code = 'queued', None
        else:
            state, error_code, provider_id = 'failed', f'http_{response.status_code}', None

    with database.session(write=True) as session:
        row = session.get(SmsOutbox, claimed['id'])
        # A status webhook may have arrived while the HTTP response was in flight.
        if row and row.state == 'sending':
            row.state, row.last_error_code, row.updated_at = state, error_code, now()
            if provider_id:
                row.provider_message_id = provider_id
    return True


def run(database, poll_seconds=2):
    while True:
        if not process_one(database):
            time.sleep(poll_seconds)
