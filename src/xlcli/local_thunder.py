from __future__ import annotations

import plistlib
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .errors import XLCLIError

THUNDER_APP = Path("/Applications/Thunder.app")
THUNDER_BUNDLE_ID = "com.xunlei.Thunder"
LOCAL_SCHEMES = frozenset({"magnet", "ed2k", "thunder", "http", "https"})


@dataclass(slots=True)
class LocalThunderStatus:
    installed: bool
    version: str = ""
    running: bool = False


def normalize_source(source: str, cwd: Path | None = None) -> str:
    if not source or any(ord(char) < 32 for char in source):
        raise XLCLIError("下载地址不能为空或包含控制字符")
    parsed = urlparse(source)
    if parsed.scheme.lower() in LOCAL_SCHEMES:
        if parsed.scheme.lower() in {"http", "https"} and not parsed.hostname:
            raise XLCLIError("HTTP 下载地址缺少有效主机名")
        return source
    base = cwd or Path.cwd()
    path = Path(source).expanduser()
    if not path.is_absolute():
        path = base / path
    path = path.resolve()
    if not path.is_file():
        raise XLCLIError(f"种子文件不存在：{path}")
    if path.suffix.lower() != ".torrent":
        raise XLCLIError("本地文件只接受 .torrent 种子")
    return str(path)


class LocalThunder:
    def __init__(
        self,
        app_path: Path = THUNDER_APP,
        runner: Callable[..., Any] = subprocess.run,
    ) -> None:
        self.app_path = app_path
        self.runner = runner

    def ensure_installed(self) -> None:
        if not self.app_path.is_dir():
            raise XLCLIError(f"未找到官方 Mac 迅雷：{self.app_path}")

    def add(self, sources: Sequence[str], background: bool = False) -> list[str]:
        self.ensure_installed()
        normalized = [normalize_source(source) for source in sources]
        if not normalized:
            raise XLCLIError("至少需要一个下载地址或种子文件")
        command = ["/usr/bin/open"]
        if background:
            command.append("-g")
        command.extend(["-b", THUNDER_BUNDLE_ID, *normalized])
        result = self.runner(command, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            message = (
                result.stderr or result.stdout or "LaunchServices 调用失败"
            ).strip()
            raise XLCLIError(f"无法提交给迅雷：{message}")
        return normalized

    def status(self) -> LocalThunderStatus:
        if not self.app_path.is_dir():
            return LocalThunderStatus(False)
        version = ""
        info = self.app_path / "Contents" / "Info.plist"
        try:
            with info.open("rb") as handle:
                version = str(
                    plistlib.load(handle).get("CFBundleShortVersionString") or ""
                )
        except (OSError, plistlib.InvalidFileException):
            pass
        running = False
        result = self.runner(
            ["/usr/bin/pgrep", "-x", "Thunder"],
            check=False,
            capture_output=True,
            text=True,
        )
        running = result.returncode == 0
        return LocalThunderStatus(True, version, running)
