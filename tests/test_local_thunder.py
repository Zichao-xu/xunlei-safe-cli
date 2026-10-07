import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from xlcli.errors import XLCLIError
from xlcli.local_thunder import LocalThunder, normalize_source


class Runner:
    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return SimpleNamespace(returncode=self.returncode, stdout="", stderr="")


class LocalThunderTests(unittest.TestCase):
    def test_protocol_links_do_not_need_an_account(self):
        for source in (
            "magnet:?xt=urn:btih:abc",
            "ed2k://|file|example|1|hash|/",
            "thunder://abc",
            "https://example.com/file.zip",
        ):
            self.assertEqual(normalize_source(source), source)

    def test_only_existing_torrent_files_are_accepted_locally(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            torrent = root / "sample.torrent"
            torrent.write_bytes(b"test")
            self.assertEqual(
                normalize_source("sample.torrent", root), str(torrent.resolve())
            )
            bad = root / "sample.txt"
            bad.write_text("test")
            with self.assertRaises(XLCLIError):
                normalize_source(str(bad))

    def test_add_uses_native_service_without_launchservices(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Thunder.app"
            app.mkdir()
            runner = Runner()
            native = Mock()
            with patch(
                "xlcli.native_thunder.NativeThunder", return_value=native
            ) as factory:
                submitted = LocalThunder(app, runner).add(
                    ["https://example.org/a"], background=True
                )
            factory.assert_called_once_with(app)
            native.add.assert_called_once_with(["https://example.org/a"])
            self.assertEqual(submitted, native.add.return_value)
            self.assertEqual(runner.calls, [])


if __name__ == "__main__":
    unittest.main()
