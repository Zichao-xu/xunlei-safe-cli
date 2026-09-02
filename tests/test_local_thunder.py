import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

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

    def test_add_invokes_official_bundle_through_launchservices(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Thunder.app"
            app.mkdir()
            runner = Runner()
            submitted = LocalThunder(app, runner).add(
                ["magnet:?xt=urn:btih:abc"], background=True
            )
            self.assertEqual(submitted, ["magnet:?xt=urn:btih:abc"])
            command = runner.calls[0][0]
            self.assertEqual(
                command[:4], ["/usr/bin/open", "-g", "-b", "com.xunlei.Thunder"]
            )
            self.assertEqual(command[4], "magnet:?xt=urn:btih:abc")


if __name__ == "__main__":
    unittest.main()
