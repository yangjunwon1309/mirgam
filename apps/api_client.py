"""HTTP boundary: no database credentials, SQL, or source CSV paths here."""
import os
import json
from pathlib import Path
import sys
from urllib.parse import urlparse
import requests


class APIClientError(Exception):
    def __init__(self, message, status=503, details=None, code='connection_failed'):
        self.message, self.status, self.details, self.code = message, status, details, code


class APIClient:
    def __init__(self, base_url=None, transport=None):
        configured_url = None
        if not base_url and not os.environ.get('MIRGAM_API_BASE_URL'):
            directory = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
            config = Path(os.environ.get('MIRGAM_CLIENT_CONFIG', str(directory / 'client.json')))
            if config.exists():
                configured_url = json.loads(config.read_text(encoding='utf-8-sig')).get('api_base_url')
        self.base_url = (base_url or os.environ.get('MIRGAM_API_BASE_URL') or configured_url or 'http://127.0.0.1:5100').rstrip('/')
        parsed = urlparse(self.base_url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Use an HTTP(S) API URL without embedded credentials')
        self.transport = transport or requests.request

    def request(self, method, path, token=None, raw=False, **kwargs):
        headers = {'Authorization': 'Bearer ' + token} if token else {}
        try:
            response = self.transport(method, self.base_url + path, headers=headers, timeout=(5, 30), **kwargs)
        except requests.RequestException as error:
            raise APIClientError('중앙 서버에 연결할 수 없습니다. 서버 실행을 확인하고 다시 시도해 주세요.') from error
        if not response.ok:
            try:
                error = response.json()['error']
            except (ValueError, KeyError, TypeError):
                error = {'message': '중앙 서버 응답을 확인하지 못했습니다.'}
            raise APIClientError(error.get('message', '요청을 처리하지 못했습니다.'), response.status_code,
                                 error.get('details'), error.get('code', 'api_error'))
        if raw:
            return response.content
        try:
            value = response.json()
            if not isinstance(value, dict) or 'data' not in value:
                raise ValueError()
            return value
        except ValueError as error:
            raise APIClientError('중앙 서버 응답 형식이 올바르지 않습니다.', code='incompatible_server') from error

    def health(self):
        result = self.request('GET', '/health')['data']
        if result.get('api_version') != 1 or result.get('ready') is not True:
            raise APIClientError('서버와 프로그램의 버전이 맞지 않습니다. 업데이트를 확인해 주세요.', code='incompatible_server')
        return result
