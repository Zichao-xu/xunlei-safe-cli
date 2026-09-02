import tempfile
import unittest
from pathlib import Path

import httpx

from xlcli.downloader import Downloader, adaptive_connections
from xlcli.errors import XLCLIError
from xlcli.models import DriveFile

DATA = bytes(range(256)) * 4096


class FakeAPI:
    def __init__(self):
        self.info_calls = 0

    def file(self, file_id):
        self.info_calls += 1
        return DriveFile(
            file_id, "payload.bin", len(DATA), "drive#file", "https://cdn.test/file"
        )

    def download_headers(self):
        return {"User-Agent": "test"}


def good_transport(request: httpx.Request) -> httpx.Response:
    value = request.headers.get("range")
    if value:
        start_text, end_text = value.removeprefix("bytes=").split("-", 1)
        start = int(start_text)
        end = int(end_text) if end_text else len(DATA) - 1
        body = DATA[start : end + 1]
        return httpx.Response(
            206,
            headers={"Content-Range": f"bytes {start}-{end}/{len(DATA)}"},
            content=body,
        )
    return httpx.Response(200, content=DATA)


class DownloaderTests(unittest.TestCase):
    def factory(self):
        return httpx.Client(transport=httpx.MockTransport(good_transport))

    def test_adaptive_connections_are_conservative(self):
        self.assertEqual(adaptive_connections(1), 1)
        self.assertEqual(adaptive_connections(100 * 1024 * 1024), 2)
        self.assertEqual(adaptive_connections(1024 * 1024 * 1024), 4)
        with self.assertRaises(XLCLIError):
            adaptive_connections(1, 8)

    def test_segmented_download_strictly_reassembles(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "result.bin"
            result = Downloader(FakeAPI(), self.factory).download(
                "file-1", target, connections=4
            )
            self.assertEqual(result.read_bytes(), DATA)
            self.assertFalse(target.with_name("result.bin.xlpart").exists())

    def test_directory_output_uses_safe_cloud_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = Downloader(FakeAPI(), self.factory).download(
                "file-1", directory, connections=2
            )
            self.assertEqual(result, (directory / "payload.bin").resolve())
            self.assertEqual(result.read_bytes(), DATA)

    def test_existing_target_is_not_overwritten_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "result.bin"
            target.write_bytes(b"keep me")
            with self.assertRaises(XLCLIError):
                Downloader(FakeAPI(), self.factory).download("file-1", target)
            self.assertEqual(target.read_bytes(), b"keep me")

    def test_wrong_content_range_is_rejected(self):
        def broken(request):
            return httpx.Response(
                206,
                headers={"Content-Range": f"bytes 1-1/{len(DATA)}"},
                content=b"x",
            )

        with tempfile.TemporaryDirectory() as tmp:
            factory = lambda: httpx.Client(transport=httpx.MockTransport(broken))
            with self.assertRaises(XLCLIError):
                Downloader(FakeAPI(), factory).download(
                    "file-1", Path(tmp) / "bad.bin", connections=4
                )


if __name__ == "__main__":
    unittest.main()
