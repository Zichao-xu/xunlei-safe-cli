import base64
import json
import plistlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from xlcli.errors import XLCLIError
from xlcli.native_thunder import NativeThunder, task_request, torrent_name


class NativeThunderTests(unittest.TestCase):
    def test_http_https_thunder_routes_to_the_same_native_task(self):
        for url in ("http://example.org/a.zip", "https://example.org/a.zip"):
            wrapped = (
                "thunder://" + base64.b64encode(("AA" + url + "ZZ").encode()).decode()
            )
            with self.subTest(url=url):
                request = task_request(url, Path("/downloads"))
                self.assertEqual(request, task_request(wrapped, Path("/downloads")))
                self.assertEqual(request["taskType"], 0)
                self.assertEqual(request["kind"], 1)
                self.assertEqual(request["fileName"], "a.zip")

    def test_encoded_traversal_and_bad_thunder_links_are_rejected(self):
        for source in (
            "http://example.org/%2e%2e",
            "http://example.org/a%2fb",
            "thunder://abc",
        ):
            with self.subTest(source=source), self.assertRaises(XLCLIError):
                task_request(source, Path("/downloads"))

    def test_bt_name_is_read_and_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.torrent"
            path.write_bytes(b"d4:infod4:name6:ok.txtee")
            self.assertEqual(torrent_name(path), "ok.txt")
            self.assertEqual(task_request(str(path), Path(tmp))["taskType"], 1)
            for value in (
                b"d4:infod4:name2:..ee",
                b"d4:infod4:name6:ok.txt5:filesld4:pathl2:..eeeeee",
                b"bad",
            ):
                path.write_bytes(value)
                with self.assertRaises(XLCLIError):
                    torrent_name(path)

    def test_batch_preflight_does_not_start_any_download_on_invalid_source(self):
        with patch.object(NativeThunder, "_prepare") as prepare:
            with self.assertRaises(XLCLIError):
                NativeThunder().add(["https://example.org/file", "thunder://bad"])
            prepare.assert_not_called()

    def test_unknown_vendor_version_fails_before_building(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Thunder.app"
            info = app / "Contents/Info.plist"
            info.parent.mkdir(parents=True)
            info.write_bytes(plistlib.dumps({"CFBundleShortVersionString": "99.0"}))
            service = (
                app / "Contents/XPCServices/DownloadService.xpc/Contents/Info.plist"
            )
            service.parent.mkdir(parents=True)
            service.write_bytes(
                plistlib.dumps({"CFBundleShortVersionString": "8.705.460"})
            )
            with patch("xlcli.native_thunder.subprocess.run") as run:
                with self.assertRaises(XLCLIError):
                    NativeThunder(app, Path(tmp) / "runtime")._prepare()
                run.assert_not_called()

    def test_stale_worker_cannot_be_reported_as_running_or_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / "jobs/xl-123456789abc"
            directory.mkdir(parents=True)
            path = directory / "job.json"
            path.write_text(
                json.dumps(
                    {
                        "updatedAt": time.time() - 100,
                        "tasks": [
                            {
                                "id": "xl-123456789abc:1",
                                "name": "sample",
                                "status": "下载中",
                                "progress": 0.5,
                                "directory": str(root),
                            },
                            {
                                "id": "xl-123456789abc:2",
                                "name": "finished",
                                "status": "已完成",
                                "progress": 1,
                                "directory": str(root),
                            },
                        ],
                    }
                )
            )
            native = NativeThunder(root=root)
            tasks = native.tasks()
            self.assertEqual(tasks[0].status, "失败")
            self.assertEqual(tasks[1].status, "已完成")
            self.assertEqual(native.wait(tasks[0].id).status, "失败")

    def test_wait_rejects_path_injection_and_unknown_task(self):
        native = NativeThunder()
        for ident in ("../../bad:1", "xl-../../123456:1", "xl-123456789abc:../../x"):
            with self.subTest(ident=ident), self.assertRaises(XLCLIError):
                native.wait(ident)


if __name__ == "__main__":
    unittest.main()
