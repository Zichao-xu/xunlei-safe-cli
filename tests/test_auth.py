import tempfile
import unittest
from pathlib import Path

import httpx

from xlcli.auth import Auth
from xlcli.errors import VerificationRequired, XLCLIError
from xlcli.storage import Settings


class MemoryTokens:
    def __init__(self):
        self.value = None

    def load(self):
        return self.value

    def save(self, token):
        self.value = token

    def clear(self):
        self.value = None


class AuthTests(unittest.TestCase):
    def make_auth(self, handler):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return Auth(
            settings=Settings(Path(self.temp.name)),
            tokens=MemoryTokens(),
            client=client,
            auth_origin="https://auth.test",
            allowed_hosts=frozenset({"auth.test"}),
        )

    def test_refresh_preserves_rotated_or_omitted_refresh_token(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "access_token": "new-access",
                    "user_id": "u1",
                    "expires_in": 7200,
                },
            )

        auth = self.make_auth(handler)
        token = auth.login_refresh_token("original-refresh")
        self.assertEqual(token.refresh_token, "original-refresh")
        self.assertEqual(auth.tokens.value.access_token, "new-access")

    def test_review_panel_is_handled_even_with_http_200(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "error": "review_panel",
                    "reviewurl": "https://i.xunlei.com/check",
                    "creditkey": "credit",
                },
            )

        auth = self.make_auth(handler)
        with self.assertRaises(VerificationRequired) as caught:
            auth.request("POST", "https://auth.test/login")
        self.assertEqual(caught.exception.credit_key, "credit")
        self.assertIn("deviceid=", caught.exception.url)

    def test_non_allowlisted_api_host_is_blocked_before_network(self):
        auth = self.make_auth(lambda request: self.fail("network must not be reached"))
        with self.assertRaises(XLCLIError):
            auth.request("GET", "https://example.com/steal")


if __name__ == "__main__":
    unittest.main()
