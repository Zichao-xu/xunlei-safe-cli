import json
import tempfile
import unittest
from pathlib import Path

from xlcli.models import Token
from xlcli.storage import ACCOUNT, SERVICE, Settings, TokenStore


class FakeErrors:
    class PasswordDeleteError(Exception):
        pass


class FakeKeyring:
    errors = FakeErrors

    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, password):
        self.values[(service, account)] = password

    def delete_password(self, service, account):
        try:
            del self.values[(service, account)]
        except KeyError as exc:
            raise self.errors.PasswordDeleteError from exc


class StorageTests(unittest.TestCase):
    def test_token_is_only_passed_to_keyring(self):
        backend = FakeKeyring()
        store = TokenStore(backend)
        token = Token("access", "refresh", "user", 123456)
        store.save(token)
        self.assertEqual(store.load(), token)
        self.assertIn("refresh", backend.values[(SERVICE, ACCOUNT)])

    def test_settings_never_contain_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(Path(tmp))
            settings.set_username("someone")
            settings.device_id()
            raw = json.loads(settings.path.read_text())
            self.assertEqual(raw["username"], "someone")
            self.assertNotIn("token", json.dumps(raw).lower())


if __name__ == "__main__":
    unittest.main()
