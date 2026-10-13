import unittest

from desktop_launcher import health_response_ready


class FakeResponse:
    def __init__(self, ok, payload):
        self.ok = ok
        self.payload = payload

    def json(self):
        return self.payload


class DesktopLauncherTests(unittest.TestCase):
    def test_health_probe_reads_api_data_envelope(self):
        self.assertTrue(health_response_ready(FakeResponse(True, {'data': {'ready': True}})))

    def test_health_probe_rejects_unhealthy_or_invalid_response(self):
        self.assertFalse(health_response_ready(FakeResponse(True, {'data': {'ready': False}})))
        self.assertFalse(health_response_ready(FakeResponse(False, {'data': {'ready': True}})))


if __name__ == '__main__':
    unittest.main()
