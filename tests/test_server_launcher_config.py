import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from server_launcher import apply_local_sms_encryption_key


class ServerLauncherConfigTests(unittest.TestCase):
    def test_loads_sms_key_from_client_config_when_environment_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'client.json'
            config_path.write_text(json.dumps({'MIRGAM_SMS_ENCRYPTION_KEY': 'local-test-key'}),
                                   encoding='utf-8')
            with patch.dict(os.environ, {}, clear=True):
                self.assertTrue(apply_local_sms_encryption_key(config_path))
                self.assertEqual(os.environ['MIRGAM_SMS_ENCRYPTION_KEY'], 'local-test-key')

    def test_environment_key_takes_precedence_over_client_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'client.json'
            config_path.write_text(json.dumps({'MIRGAM_SMS_ENCRYPTION_KEY': 'json-key'}), encoding='utf-8')
            with patch.dict(os.environ, {'MIRGAM_SMS_ENCRYPTION_KEY': 'environment-key'}, clear=True):
                self.assertTrue(apply_local_sms_encryption_key(config_path))
                self.assertEqual(os.environ['MIRGAM_SMS_ENCRYPTION_KEY'], 'environment-key')

    def test_frozen_server_reads_sibling_mirgam_client_config(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory)
            client_dir = package / 'Mirgam'
            server_dir = package / 'MirgamServer'
            client_dir.mkdir()
            server_dir.mkdir()
            (client_dir / 'client.json').write_text(
                json.dumps({'MIRGAM_SMS_ENCRYPTION_KEY': 'packaged-test-key'}), encoding='utf-8')
            executable = server_dir / 'MirgamServer.exe'
            with patch.dict(os.environ, {}, clear=True), \
                    patch.object(sys, 'frozen', True, create=True), \
                    patch.object(sys, 'executable', str(executable)):
                self.assertTrue(apply_local_sms_encryption_key())
                self.assertEqual(os.environ['MIRGAM_SMS_ENCRYPTION_KEY'], 'packaged-test-key')


if __name__ == '__main__':
    unittest.main()
