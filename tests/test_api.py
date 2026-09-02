import unittest

import httpx

from xlcli.api import DriveAPI, RateGate, validate_source_url
from xlcli.errors import XLCLIError
from xlcli.models import Token


class MemoryTokens:
    def save(self, token):
        self.value = token


class FakeAuth:
    def __init__(self):
        self.device_id = "d" * 32
        self.captcha_token = "captcha"
        self.tokens = MemoryTokens()
        self._token = Token("access", "refresh", "user", 9999999999)

    def _headers(self):
        return {
            "User-Agent": "api-agent",
            "x-client-id": "client",
            "x-client-version": "version",
        }

    def authorization(self):
        return "Bearer access"

    def ensure_captcha(self, action):
        if not self.captcha_token:
            self.captcha_token = "captcha-refreshed"
        return self.captcha_token

    def token(self):
        return self._token

    def _refresh(self, refresh_token):
        return self._token


class APITests(unittest.TestCase):
    def test_source_scheme_allowlist(self):
        for url in (
            "magnet:?xt=urn:btih:abc",
            "ed2k://abc",
            "thunder://abc",
            "https://example.com/a",
        ):
            validate_source_url(url)
        for url in ("file:///etc/passwd", "javascript:alert(1)", "https:///missing"):
            with self.assertRaises(XLCLIError):
                validate_source_url(url)

    def test_add_offline_uses_auth_and_expected_payload(self):
        seen = {}

        def handler(request):
            seen["request"] = request
            return httpx.Response(
                200,
                json={
                    "task": {
                        "id": "task-1",
                        "phase": "PHASE_TYPE_PENDING",
                        "name": "sample",
                    }
                },
            )

        api = DriveAPI(
            FakeAuth(),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            drive_origin="https://api.test",
            allowed_hosts=frozenset({"api.test"}),
            gate=RateGate(0),
        )
        task = api.add_offline("magnet:?xt=urn:btih:abc")
        request = seen["request"]
        self.assertEqual(task.id, "task-1")
        self.assertEqual(request.headers["authorization"], "Bearer access")
        self.assertEqual(request.headers["x-captcha-token"], "captcha")
        self.assertIn(b"UPLOAD_TYPE_URL", request.content)

    def test_download_headers_do_not_leak_bearer_token_to_cdn(self):
        api = DriveAPI(
            FakeAuth(),
            client=httpx.Client(transport=httpx.MockTransport(lambda request: None)),
            drive_origin="https://api.test",
            allowed_hosts=frozenset({"api.test"}),
            gate=RateGate(0),
        )
        headers = {key.lower(): value for key, value in api.download_headers().items()}
        self.assertNotIn("authorization", headers)


if __name__ == "__main__":
    unittest.main()
