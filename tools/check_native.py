"""Exercise the real installed Thunder kernel without opening its UI."""

import json
import os
import signal
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from xlcli.native_thunder import NativeThunder


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    with tempfile.TemporaryDirectory(prefix="xl-native-check-") as tmp:
        root = Path(tmp)
        payload = b"Thunder native background acceptance\n" * 1024
        (root / "sample.bin").write_bytes(payload)
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), partial(QuietHandler, directory=tmp)
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        native = NativeThunder(root=root / "runtime")
        try:
            task = native.add(
                [f"http://127.0.0.1:{server.server_port}/sample.bin"], root / "output"
            )[0]
            task = native.wait(task.id, 30)
            if task.status != "已完成":
                raise RuntimeError(task.message)
            files = list((root / "output").rglob("sample.bin"))
            if len(files) != 1 or files[0].read_bytes() != payload:
                raise RuntimeError("Downloaded content differs from fixture")
            print(
                "PASS: official Thunder kernel completed background HTTP download; content matches"
            )
        finally:
            server.shutdown()
            server.server_close()
            # Only terminate hosts started under this test's private runtime directory.
            for path in (root / "runtime/jobs").glob("*/job.json"):
                job = json.loads(path.read_text())
                pid = job.get("pid")
                if pid and not job.get("stopped"):
                    os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)


if __name__ == "__main__":
    main()
