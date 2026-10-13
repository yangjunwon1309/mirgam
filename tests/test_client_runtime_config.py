import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from apps.api_client import APIClient, apply_client_runtime_settings
from apps.app import create_app


class ClientRuntimeConfigTests(unittest.TestCase):
    def test_server_list_is_preserved_when_api_url_is_also_configured(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'client.json'
            config_path.write_text(json.dumps({
                'MIRGAM_API_BASE_URL': 'https://api.example.test',
                'default_server_id': 'render',
                'servers': [
                    {'id': 'render', 'name': 'Render', 'url': 'https://render.example.test'},
                    {'id': 'local', 'name': '이 PC의 로컬 서버', 'url': 'http://127.0.0.1:5100'},
                ],
            }), encoding='utf-8')
            with patch.dict(os.environ, {'MIRGAM_CLIENT_CONFIG': str(config_path)}):
                app = create_app(testing=True)
            self.assertEqual([server['id'] for server in app.extensions['api_servers']], ['render', 'local'])

    def test_json_runtime_settings_are_loaded_and_environment_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'client.json'
            config_path.write_text(json.dumps({
                'MIRGAM_API_BASE_URL': 'https://api.example.test',
                'MIRGAM_SMS_ENCRYPTION_KEY': 'json-secret',
            }), encoding='utf-8')
            with patch.dict(os.environ, {'MIRGAM_CLIENT_CONFIG': str(config_path)}, clear=True):
                apply_client_runtime_settings()
                self.assertEqual(os.environ['MIRGAM_API_BASE_URL'], 'https://api.example.test')
                self.assertEqual(os.environ['MIRGAM_SMS_ENCRYPTION_KEY'], 'json-secret')
                self.assertEqual(APIClient().base_url, 'https://api.example.test')

            with patch.dict(os.environ, {
                'MIRGAM_CLIENT_CONFIG': str(config_path),
                'MIRGAM_API_BASE_URL': 'https://environment.example.test',
                'MIRGAM_SMS_ENCRYPTION_KEY': 'environment-secret',
            }, clear=True):
                apply_client_runtime_settings()
                self.assertEqual(os.environ['MIRGAM_API_BASE_URL'], 'https://environment.example.test')
                self.assertEqual(os.environ['MIRGAM_SMS_ENCRYPTION_KEY'], 'environment-secret')


if __name__ == '__main__':
    unittest.main()
