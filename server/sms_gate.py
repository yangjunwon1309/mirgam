"""Small, server-only SMS Gateway API and webhook security helpers."""
import hashlib
import hmac
import json
import os
import re
from urllib.parse import urlsplit

import requests
from cryptography.fernet import Fernet, InvalidToken


SMS_GATE_DEFAULT_URL = 'https://api.sms-gate.app'
SMS_GATE_EVENTS = ('sms:received', 'sms:sent', 'sms:delivered', 'sms:failed')


class SmsGateError(Exception):
    pass


def cipher():
    key = os.environ.get('MIRGAM_SMS_ENCRYPTION_KEY', '').strip()
    if not key:
        raise SmsGateError('서버의 문자 연동 암호화 키가 설정되지 않았습니다.')
    try:
        return Fernet(key.encode('ascii'))
    except (ValueError, UnicodeEncodeError) as error:
        raise SmsGateError('서버의 문자 연동 암호화 키 형식이 올바르지 않습니다.') from error


def encrypt_secret(value):
    return cipher().encrypt(value.encode('utf-8')).decode('ascii')


def decrypt_secret(value):
    try:
        return cipher().decrypt(value.encode('ascii')).decode('utf-8')
    except (InvalidToken, ValueError, UnicodeEncodeError) as error:
        raise SmsGateError('문자 연동 비밀정보를 해독할 수 없습니다. 암호화 키 설정을 확인해 주세요.') from error


def normalize_base_url(value):
    parts = urlsplit((value or '').strip())
    if (parts.scheme != 'https' or parts.hostname != 'api.sms-gate.app' or parts.username or parts.password or
            parts.query or parts.fragment or parts.path not in ('', '/', '/mobile/v1', '/3rdparty/v1')):
        raise SmsGateError('Cloud Server 주소는 https://api.sms-gate.app 또는 https://api.sms-gate.app/mobile/v1 이어야 합니다.')
    return SMS_GATE_DEFAULT_URL


def api_call(base_url, username, password, method, path, *, json_body=None):
    url = normalize_base_url(base_url) + '/3rdparty/v1' + path
    try:
        response = requests.request(method, url, auth=(username, password), json=json_body,
                                    timeout=(5, 20), allow_redirects=False)
    except requests.RequestException as error:
        raise SmsGateError('SMS Gateway 서버에 연결하지 못했습니다.') from error
    if not 200 <= response.status_code < 300:
        raise SmsGateError(f'SMS Gateway 요청이 거절되었습니다. HTTP {response.status_code}')
    if response.content:
        try:
            return response.json()
        except ValueError as error:
            raise SmsGateError('SMS Gateway 응답을 읽지 못했습니다.') from error
    return {}


def verify_webhook(signing_key, raw_body, timestamp, signature, now_seconds):
    if not timestamp or not signature or not timestamp.isdigit():
        return False
    if abs(now_seconds - int(timestamp)) > 300:
        return False
    expected = hmac.new(signing_key.encode('utf-8'), raw_body + timestamp.encode('ascii'), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


def response_devices(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ('data', 'devices', 'items'):
            if isinstance(value.get(key), list):
                return value[key]
    return []


def device_identifier(device):
    if not isinstance(device, dict):
        return ''
    return str(device.get('id') or device.get('deviceId') or device.get('device_id') or '')


def message_identifier(value):
    if not isinstance(value, dict):
        return ''
    return str(value.get('id') or value.get('webhookId') or value.get('webhook_id') or value.get('messageId') or '')


def parse_event_body(value):
    if not isinstance(value, dict):
        return None
    payload = value.get('payload')
    if not isinstance(payload, dict):
        return None
    return payload


def phone_digits(value):
    return ''.join(character for character in str(value or '') if character.isdigit())


ORDER_KEYWORDS = (
    '주문', '쥬문', '보내', '보네', '부탁', '주세요', '주셔', '살게', '살께', '구매', '신청',
    '추가해', '넣어주', '담아주', '챙겨주', '부쳐주', '발송', '배송해',
)
ORDER_FUZZY_PHRASES = (
    '보내주세요', '보내주실래요', '부탁드립니다', '부탁드려요', '구매할게요', '신청할게요',
    '넣어주세요', '담아주세요', '챙겨주세요', '택배보내주세요',
)


def _normalized_message(value):
    return re.sub(r'[^0-9a-z가-힣]+', '', str(value or '').casefold())


def _within_one_edit(left, right):
    if abs(len(left) - len(right)) > 1:
        return False
    i = j = differences = 0
    while i < len(left) and j < len(right):
        if left[i] == right[j]:
            i += 1
            j += 1
            continue
        differences += 1
        if differences > 1:
            return False
        if len(left) > len(right):
            i += 1
        elif len(right) > len(left):
            j += 1
        else:
            i += 1
            j += 1
    if i < len(left) or j < len(right):
        differences += 1
    return differences <= 1


def _contains_typo_tolerant_phrase(text, phrase):
    phrase = _normalized_message(phrase)
    if len(phrase) < 5:
        return False
    for window_size in (len(phrase) - 1, len(phrase), len(phrase) + 1):
        for start in range(max(0, len(text) - window_size + 1)):
            if _within_one_edit(text[start:start + window_size], phrase):
                return True
    return False


def has_order_intent(body):
    """Match common Korean order wording, spacing variants, and one-character typos."""
    normalized = _normalized_message(body)
    return (any(_normalized_message(word) in normalized for word in ORDER_KEYWORDS) or
            any(_contains_typo_tolerant_phrase(normalized, phrase) for phrase in ORDER_FUZZY_PHRASES))


def maybe_order_message(body, products):
    """Return product/quantity suggestions for likely orders; users still confirm them."""
    text = str(body or '').strip()
    if not text or len(text) > 5000:
        return None, ''
    lowered = text.casefold()
    matched = next((product for product in products if product.item and product.item.casefold() in lowered), None)
    # A known product name OR common order wording is enough to enter manual review.
    if matched is None and not has_order_intent(text):
        return None, ''
    quantity = ''
    if matched:
        escaped = re.escape(matched.item)
        pattern = re.compile(rf'(?:{escaped}\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:개|박스|상자|kg)?\s*{escaped})', re.I)
        found = pattern.search(text)
        if found:
            quantity = found.group(1) or found.group(2) or ''
    return matched, quantity


def json_object(value):
    try:
        result = json.loads(value or '{}')
    except (TypeError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}
