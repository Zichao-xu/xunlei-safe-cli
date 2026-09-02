from __future__ import annotations

import concurrent.futures
import hashlib
import os
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .api import DriveAPI
from .errors import DownloadLinkExpired, XLCLIError
from .models import DriveFile

Progress = Callable[[int, int, float], None]
CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+|\*)", re.IGNORECASE)


def adaptive_connections(size: int, requested: int = 0) -> int:
    if requested:
        if requested < 1 or requested > 4:
            raise XLCLIError("连接数必须在 1–4 之间")
        return requested
    if size < 64 * 1024 * 1024:
        return 1
    if size < 512 * 1024 * 1024:
        return 2
    return 4


class Downloader:
    def __init__(
        self,
        api: DriveAPI,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self.api = api
        self.client_factory = client_factory or self._default_client

    @staticmethod
    def _default_client() -> httpx.Client:
        return httpx.Client(
            timeout=httpx.Timeout(300, connect=30),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=6, max_keepalive_connections=4),
        )

    @staticmethod
    def _validate_download_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise XLCLIError("迅雷返回了无效的下载地址")
        # Download CDN hosts vary. HTTPS is mandatory, but credentials are never
        # sent outside hosts returned directly by the authenticated drive API.

    def _supports_range(
        self, client: httpx.Client, file: DriveFile, headers: dict[str, str]
    ) -> bool:
        with client.stream(
            "GET", file.download_url, headers={**headers, "Range": "bytes=0-0"}
        ) as response:
            if response.status_code in {401, 403, 410}:
                raise DownloadLinkExpired("下载直链已失效")
            if response.status_code != 206:
                return False
            match = CONTENT_RANGE.fullmatch(response.headers.get("content-range", ""))
            if not match or int(match.group(1)) != 0 or int(match.group(2)) != 0:
                return False
            # Read only the advertised byte. Never buffer an ignored Range request.
            iterator = response.iter_bytes()
            first = next(iterator, b"")
            return len(first) == 1

    def download(
        self,
        file_id: str,
        output: Path | None = None,
        connections: int = 0,
        verify_md5: bool = False,
        progress: Progress | None = None,
        overwrite: bool = False,
    ) -> Path:
        last_error: Exception | None = None
        for link_attempt in range(2):
            file = self.api.file(file_id)
            if not file.download_url:
                raise XLCLIError("迅雷没有为该文件提供下载地址")
            self._validate_download_url(file.download_url)
            safe_name = Path(file.name.replace("\x00", "")).name or "download.bin"
            requested = output.expanduser() if output else Path(safe_name)
            if requested.exists() and requested.is_dir():
                requested = requested / safe_name
            target = requested.resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and not overwrite:
                raise XLCLIError(
                    f"目标文件已存在，不会覆盖：{target}（需要时使用 --force）"
                )
            partial = target.with_name(target.name + ".xlpart")
            headers = self.api.download_headers()
            try:
                with self.client_factory() as client:
                    can_range = self._supports_range(client, file, headers)
                workers = adaptive_connections(file.size, connections)
                if not can_range:
                    workers = 1
                if workers == 1:
                    self._single(file, partial, headers, progress, can_range)
                else:
                    self._segmented(file, partial, headers, workers, progress)
                self._verify(partial, file, verify_md5)
                os.replace(partial, target)
                return target
            except DownloadLinkExpired as exc:
                last_error = exc
                if link_attempt == 0:
                    time.sleep(1)
                    continue
                break
        raise XLCLIError(f"下载失败：{last_error or '无法刷新下载直链'}")

    def _single(
        self,
        file: DriveFile,
        partial: Path,
        headers: dict[str, str],
        progress: Progress | None,
        can_range: bool,
    ) -> None:
        start = time.monotonic()
        last_error: Exception | None = None
        for attempt in range(3):
            downloaded = partial.stat().st_size if partial.exists() and can_range else 0
            mode = "ab" if downloaded else "wb"
            request_headers = dict(headers)
            if downloaded:
                request_headers["Range"] = f"bytes={downloaded}-"
            try:
                with (
                    self.client_factory() as client,
                    client.stream(
                        "GET", file.download_url, headers=request_headers
                    ) as response,
                ):
                    if response.status_code in {401, 403, 410}:
                        raise DownloadLinkExpired("下载直链已失效")
                    if downloaded and response.status_code != 206:
                        downloaded = 0
                        mode = "wb"
                    response.raise_for_status()
                    with partial.open(mode) as handle:
                        for chunk in response.iter_bytes(256 * 1024):
                            handle.write(chunk)
                            downloaded += len(chunk)
                            if progress:
                                elapsed = max(time.monotonic() - start, 0.001)
                                progress(downloaded, file.size, downloaded / elapsed)
                return
            except DownloadLinkExpired:
                raise
            except (httpx.HTTPError, OSError) as exc:
                last_error = exc
                if attempt == 2:
                    break
                time.sleep(2**attempt)
        raise XLCLIError(f"下载连接连续失败：{last_error}")

    def _segmented(
        self,
        file: DriveFile,
        partial: Path,
        headers: dict[str, str],
        workers: int,
        progress: Progress | None,
    ) -> None:
        piece_size = (file.size + workers - 1) // workers
        ranges = [
            (
                index,
                index * piece_size,
                min(file.size - 1, (index + 1) * piece_size - 1),
            )
            for index in range(workers)
            if index * piece_size < file.size
        ]
        temp_dir = Path(tempfile.mkdtemp(prefix="xlcli-pieces-"))
        completed = [0] * len(ranges)
        lock = threading.Lock()
        start_time = time.monotonic()

        def fetch(index: int, start: int, end: int) -> Path:
            piece = temp_dir / f"{index:02d}.part"
            expected = end - start + 1
            for attempt in range(3):
                received = 0
                try:
                    with (
                        self.client_factory() as client,
                        client.stream(
                            "GET",
                            file.download_url,
                            headers={**headers, "Range": f"bytes={start}-{end}"},
                        ) as response,
                    ):
                        if response.status_code in {401, 403, 410}:
                            raise DownloadLinkExpired("下载直链已失效")
                        if response.status_code != 206:
                            raise XLCLIError(
                                "服务器未正确响应分段请求，已停止以防文件损坏"
                            )
                        match = CONTENT_RANGE.fullmatch(
                            response.headers.get("content-range", "")
                        )
                        if not match or (
                            int(match.group(1)),
                            int(match.group(2)),
                        ) != (start, end):
                            raise XLCLIError("服务器返回了错误的分段范围")
                        with piece.open("wb") as handle:
                            for chunk in response.iter_bytes(256 * 1024):
                                handle.write(chunk)
                                received += len(chunk)
                                with lock:
                                    completed[index] = received
                                    if progress:
                                        total = sum(completed)
                                        elapsed = max(
                                            time.monotonic() - start_time, 0.001
                                        )
                                        progress(total, file.size, total / elapsed)
                    if received != expected:
                        raise XLCLIError(
                            f"分段 {index + 1} 大小不符：应为 {expected}，实际 {received}"
                        )
                    return piece
                except DownloadLinkExpired:
                    raise
                except (httpx.HTTPError, XLCLIError):
                    if attempt == 2:
                        raise
                    time.sleep(2**attempt)
            raise XLCLIError("分段下载失败")

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(fetch, *item) for item in ranges]
                pieces = [future.result() for future in futures]
            with partial.open("wb") as output:
                for piece in pieces:
                    with piece.open("rb") as source:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @staticmethod
    def _verify(path: Path, file: DriveFile, verify_md5: bool) -> None:
        actual = path.stat().st_size
        if file.size and actual != file.size:
            raise XLCLIError(f"文件大小校验失败：应为 {file.size}，实际 {actual}")
        expected = file.md5.lower()
        if verify_md5 and re.fullmatch(r"[0-9a-f]{32}", expected):
            digest = hashlib.md5(usedforsecurity=False)
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != expected:
                raise XLCLIError("MD5 校验失败")
