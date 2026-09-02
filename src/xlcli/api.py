from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from .auth import ALLOWED_HOSTS, DOWNLOAD_USER_AGENT, Auth
from .errors import XLCLIError
from .models import DriveFile, OfflineTask

DRIVE_ORIGIN = "https://api-pan.xunlei.com"
PHASES = {
    "PHASE_TYPE_PENDING": "等待中",
    "PHASE_TYPE_RUNNING": "离线中",
    "PHASE_TYPE_COMPLETE": "已完成",
    "PHASE_TYPE_ERROR": "失败",
}
SOURCE_SCHEMES = frozenset({"magnet", "ed2k", "thunder", "http", "https"})


def validate_source_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme.lower() not in SOURCE_SCHEMES:
        raise XLCLIError("仅支持 magnet、ed2k、thunder、HTTP 或 HTTPS 地址")
    if parsed.scheme.lower() in {"http", "https"} and not parsed.hostname:
        raise XLCLIError("HTTP 下载地址缺少有效主机名")


class RateGate:
    """Serialises API calls and prevents accidental request bursts."""

    def __init__(self, interval: float = 0.4) -> None:
        self.interval = interval
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            delay = self.interval - (time.monotonic() - self._last)
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()


class DriveAPI:
    def __init__(
        self,
        auth: Auth,
        client: httpx.Client | None = None,
        drive_origin: str = DRIVE_ORIGIN,
        allowed_hosts: frozenset[str] = ALLOWED_HOSTS,
        gate: RateGate | None = None,
    ) -> None:
        self.auth = auth
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(30), follow_redirects=False
        )
        self.drive_origin = drive_origin.rstrip("/")
        self.allowed_hosts = allowed_hosts
        self.gate = gate or RateGate()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.drive_origin}/drive/v1/{path.lstrip('/')}"
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in self.allowed_hosts:
            raise XLCLIError(
                f"安全策略阻止访问非迅雷官方地址：{parsed.hostname or url}"
            )
        self.gate.wait()
        action = f"{method.upper()}:/drive/v1/{path.split('?')[0]}"
        headers = {
            "User-Agent": self.auth._headers()["User-Agent"],
            "Accept": "application/json;charset=UTF-8",
            "x-device-id": self.auth.device_id,
            "x-client-id": self.auth._headers()["x-client-id"],
            "x-client-version": self.auth._headers()["x-client-version"],
            "Authorization": self.auth.authorization(),
            "X-Captcha-Token": self.auth.ensure_captcha(action),
        }
        headers.update(kwargs.pop("headers", {}))
        response = self.client.request(method, url, headers=headers, **kwargs)
        try:
            first_data = response.json()
        except ValueError:
            first_data = {}
        if first_data.get("error") == "captcha_invalid":
            self.auth.captcha_token = ""
            headers["X-Captcha-Token"] = self.auth.ensure_captcha(action)
            self.gate.wait()
            response = self.client.request(method, url, headers=headers, **kwargs)
        elif response.status_code == 401:
            current = self.auth.token()
            if not current.refresh_token:
                raise XLCLIError("登录已过期且无法刷新，请重新登录")
            refreshed = self.auth._refresh(current.refresh_token)
            self.auth.tokens.save(refreshed)
            self.auth._token = refreshed
            headers["Authorization"] = self.auth.authorization()
            self.gate.wait()
            response = self.client.request(method, url, headers=headers, **kwargs)
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            error = data.get("error") or f"HTTP {response.status_code}"
            detail = data.get("error_description") or data.get("message") or ""
            raise XLCLIError(f"迅雷云盘错误：{error} {detail}".strip())
        if data.get("error") and data.get("error") != "success":
            raise XLCLIError(
                f"迅雷云盘错误：{data.get('error')} "
                f"{data.get('error_description', '')}".strip()
            )
        return data

    @staticmethod
    def _file(data: dict[str, Any]) -> DriveFile:
        download_url = ""
        links = data.get("links") or {}
        candidate = (
            links.get("application/octet-stream") if isinstance(links, dict) else None
        )
        if isinstance(candidate, dict):
            download_url = str(candidate.get("url") or "")
        elif isinstance(candidate, str):
            download_url = candidate
        if not download_url:
            web = data.get("web_content_link") or ""
            if isinstance(web, dict):
                download_url = str(web.get("url") or "")
            elif isinstance(web, str):
                download_url = web
        if not download_url:
            for media in data.get("medias") or []:
                if isinstance(media, dict):
                    link = media.get("link") or {}
                    if isinstance(link, dict) and link.get("url"):
                        download_url = str(link["url"])
                        break
        raw_hash = data.get("hash") or data.get("md5") or ""
        if isinstance(raw_hash, dict):
            raw_hash = raw_hash.get("md5") or raw_hash.get("value") or ""
        return DriveFile(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or "未命名"),
            size=int(data.get("size") or 0),
            kind=str(data.get("kind") or "drive#file"),
            download_url=download_url,
            md5=str(raw_hash),
        )

    @staticmethod
    def _task(data: dict[str, Any]) -> OfflineTask:
        phase = str(data.get("phase") or "")
        progress = data.get("progress") or 0
        try:
            progress_number = float(progress)
        except (TypeError, ValueError):
            progress_number = 0
        nested_file = data.get("file") or {}
        nested_file_id = nested_file.get("id") if isinstance(nested_file, dict) else ""
        return OfflineTask(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or data.get("file_name") or "未命名"),
            status=PHASES.get(phase, phase or "未知"),
            progress=progress_number,
            file_id=str(
                data.get("file_id")
                or data.get("target_file_id")
                or nested_file_id
                or ""
            ),
            message=str(data.get("message") or data.get("error_description") or ""),
        )

    def add_offline(self, url: str, parent_id: str = "") -> OfflineTask:
        validate_source_url(url)
        payload: dict[str, Any] = {
            "kind": "drive#file",
            "name": "",
            "upload_type": "UPLOAD_TYPE_URL",
            "url": {"url": url},
        }
        if parent_id:
            payload["parent_id"] = parent_id
        data = self._request("POST", "files", json=payload)
        return self._task(data.get("task") or data)

    def task(self, task_id: str) -> OfflineTask:
        return self._task(self._request("GET", f"tasks/{task_id}"))

    def tasks(self, limit: int = 30) -> list[OfflineTask]:
        data = self._request(
            "GET", "tasks", params={"type": "offline", "limit": min(limit, 100)}
        )
        return [self._task(item) for item in data.get("tasks") or []]

    def wait_task(
        self, task_id: str, timeout: int = 3600, poll_interval: float = 3
    ) -> OfflineTask:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            task = self.task(task_id)
            if task.status in {"已完成", "失败"}:
                return task
            time.sleep(max(2.0, poll_interval))
        raise XLCLIError(f"等待离线任务超时：{task_id}")

    def file(self, file_id: str) -> DriveFile:
        return self._file(self._request("GET", f"files/{file_id}"))

    def files(self, parent_id: str = "", limit: int = 100) -> list[DriveFile]:
        data = self._request(
            "GET",
            "files",
            params={
                "parent_id": parent_id,
                "limit": min(limit, 100),
                "page_token": "",
                "refresh": "true",
                "__sync": "true",
                "with_audit": "true",
                "filters": '{"phase":{"eq":"PHASE_TYPE_COMPLETE"},"trashed":{"eq":false}}',
            },
        )
        return [self._file(item) for item in data.get("files") or []]

    def download_headers(self) -> dict[str, str]:
        return {
            "User-Agent": DOWNLOAD_USER_AGENT,
            "Accept": "*/*",
        }

    def close(self) -> None:
        self.client.close()
