from __future__ import annotations

import base64
import binascii
import fcntl
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

from .errors import XLCLIError
from .local_thunder import THUNDER_APP, normalize_source
from .models import OfflineTask

SUPPORTED_APP = "5.80.0"
SUPPORTED_SERVICE = "8.705.460"
ROOT = Path.home() / "Library/Application Support/xlcli/native"
SOURCE = Path(__file__).with_name("native") / "host.m"


def task_request(source: str, directory: Path) -> dict:
    source = normalize_source(source)
    parsed = urlparse(source)
    if parsed.scheme.lower() == "thunder":
        try:
            payload = source.split("://", 1)[1]
            decoded = base64.b64decode(payload, validate=True).decode("utf-8")
            if not decoded.startswith("AA") or not decoded.endswith("ZZ"):
                raise ValueError
            source = normalize_source(decoded[2:-2])
            parsed = urlparse(source)
        except (IndexError, ValueError, UnicodeError, binascii.Error):
            raise XLCLIError("迅雷链接格式无效") from None
        if parsed.scheme.lower() == "thunder":
            raise XLCLIError("迅雷链接不能嵌套")
    kind = parsed.scheme.lower()
    request = {
        "taskType": {"magnet": 4, "ed2k": 2}.get(kind, 0),
        "kind": 1,
        "source": "xlcli",
        "entryId": "xlcli-native",
        "saveDirPath": str(directory) + "/",
    }
    if kind not in {"http", "https", "magnet", "ed2k"}:
        request.update(taskType=1, seedPath=source, fileName=torrent_name(Path(source)))
    else:
        request["url"] = source
    if kind in {"http", "https"}:
        name = unquote(Path(parsed.path).name) or "download"
        if (
            name in {".", ".."}
            or any(c in name for c in "/\\")
            or any(ord(c) < 32 for c in name)
        ):
            raise XLCLIError("下载文件名无效")
        request["fileName"] = name
    return request


def torrent_name(path: Path) -> str:
    """Validate names before giving a torrent to the native engine."""
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            raise XLCLIError("种子文件超过 16 MiB")
        data = path.read_bytes()
    except OSError:
        raise XLCLIError("无法读取种子文件") from None
    if len(data) > 16 * 1024 * 1024:
        raise XLCLIError("种子文件超过 16 MiB")

    def decode(offset=0, depth=0):
        if depth > 40:
            raise ValueError
        kind = data[offset : offset + 1]
        if kind == b"i":
            end = data.index(b"e", offset)
            return int(data[offset + 1 : end]), end + 1
        if kind in {b"l", b"d"}:
            result = [] if kind == b"l" else {}
            offset += 1
            while data[offset : offset + 1] != b"e":
                value, offset = decode(offset, depth + 1)
                if kind == b"d":
                    if not isinstance(value, bytes):
                        raise ValueError
                    item, offset = decode(offset, depth + 1)
                    result[value] = item
                else:
                    result.append(value)
            return result, offset + 1
        end = data.index(b":", offset)
        size = int(data[offset:end])
        if size < 0 or end + 1 + size > len(data):
            raise ValueError
        return data[end + 1 : end + 1 + size], end + 1 + size

    def safe_name(value):
        name = value.decode("utf-8") if isinstance(value, bytes) else ""
        if (
            not name
            or name in {".", ".."}
            or any(c in name for c in "/\\")
            or any(ord(c) < 32 for c in name)
        ):
            raise ValueError
        return name

    try:
        torrent, end = decode()
        if end != len(data):
            raise ValueError
        info = torrent[b"info"]
        name = safe_name(info.get(b"name.utf-8", info[b"name"]))
        for item in info.get(b"files", []):
            parts = item.get(b"path.utf-8", item[b"path"])
            if not parts:
                raise ValueError
            for part in parts:
                safe_name(part)
        return name
    except (ValueError, IndexError, KeyError, TypeError, AttributeError, UnicodeError):
        raise XLCLIError("种子格式或文件名无效") from None


class NativeThunder:
    """Host the installed official kernel in a private, background-only app bundle."""

    def __init__(self, app_path: Path = THUNDER_APP, root: Path = ROOT):
        self.app_path = app_path
        self.root = root

    def _prepare(self) -> Path:
        service = self.app_path / "Contents/XPCServices/DownloadService.xpc"
        try:
            app_info = plistlib.loads(
                (self.app_path / "Contents/Info.plist").read_bytes()
            )
            info = plistlib.loads((service / "Contents/Info.plist").read_bytes())
        except (OSError, plistlib.InvalidFileException):
            raise XLCLIError("未找到完整的官方 Mac 迅雷及原生下载服务") from None
        if (
            app_info.get("CFBundleShortVersionString") != SUPPORTED_APP
            or info.get("CFBundleShortVersionString") != SUPPORTED_SERVICE
        ):
            raise XLCLIError("当前仅验证迅雷 5.80.0；其他版本暂不调用后台接口")
        try:
            source = SOURCE.read_bytes()
            binary = service / "Contents/MacOS/DownloadService"
            fingerprint = hashlib.sha256(source + binary.read_bytes()).hexdigest()[:20]
        except OSError:
            raise XLCLIError("迅雷后台服务或 xl 原生调用文件缺失") from None
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        bundle = self.root / "hosts" / fingerprint / "XLThunderHost.app"
        executable = bundle / "Contents/MacOS/XLThunderHost"
        with (self.root / "build.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if executable.is_file() and (bundle / "ready").is_file():
                return executable
            contents = bundle / "Contents"
            (contents / "MacOS").mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                shutil.copytree(
                    service,
                    contents / "XPCServices/DownloadService.xpc",
                    dirs_exist_ok=True,
                )
                (contents / "Info.plist").write_bytes(
                    plistlib.dumps(
                        {
                            "CFBundleIdentifier": "icu.aminoac.xlcli.nativehost",
                            "CFBundleExecutable": "XLThunderHost",
                            "CFBundlePackageType": "APPL",
                            "LSBackgroundOnly": True,
                        }
                    )
                )
                for command in (
                    ["/usr/bin/codesign", "--verify", "--strict", str(service)],
                    [
                        "/usr/bin/xcrun",
                        "clang",
                        "-fobjc-arc",
                        "-framework",
                        "Foundation",
                        str(SOURCE),
                        "-o",
                        str(executable),
                    ],
                    ["/usr/bin/codesign", "--force", "--sign", "-", str(bundle)],
                ):
                    subprocess.run(command, check=True, capture_output=True, text=True)
                (bundle / "ready").touch(mode=0o600)
            except (OSError, subprocess.CalledProcessError):
                raise XLCLIError(
                    "无法准备迅雷后台服务；需要有效的官方迅雷和 Apple 命令行工具"
                ) from None
        return executable

    def _load(self, path: Path) -> dict:
        try:
            job = json.loads(path.read_text())
        except (OSError, ValueError):
            raise XLCLIError("无法读取迅雷后台任务记录") from None
        # Do not leave old jobs falsely showing 'running' after a reboot or crash.
        if not job.get("stopped") and time.time() - job.get("updatedAt", 0) > 45:
            for task in job["tasks"]:
                if task["status"] not in {"已完成", "失败"}:
                    task.update(status="失败", message="后台下载已中断，请重新添加任务")
        return job

    def add(self, sources: list[str], output: Path | None = None) -> list[OfflineTask]:
        if not sources:
            raise XLCLIError("至少需要一个下载地址或种子文件")
        # Validate the entire batch before starting any native task.
        requests = [task_request(source, Path("/unused")) for source in sources]
        destination = (output or Path.home() / "Downloads/xl").expanduser().resolve()
        if destination.exists() and not destination.is_dir():
            raise XLCLIError("本机模式的 output 请指定保存目录")
        executable = self._prepare()
        job_id = "xl-" + uuid.uuid4().hex[:12]
        directory = self.root / "jobs" / job_id
        config = directory / "config"
        config.mkdir(parents=True, mode=0o700)
        downloads = destination / (time.strftime("%Y%m%d-%H%M%S-") + job_id[3:9])
        downloads.mkdir(parents=True, mode=0o700)
        tasks = []
        for index, request in enumerate(requests, 1):
            target = downloads / str(index)
            target.mkdir(mode=0o700)
            request["saveDirPath"] = str(target) + "/"
            tasks.append(
                {
                    "id": f"{job_id}:{index}",
                    "name": request.get("fileName", "种子 / 磁力下载"),
                    "status": "准备中",
                    "message": "",
                    "progress": 0,
                    "directory": str(target),
                    "request": request,
                }
            )
        path = directory / "job.json"
        job = {
            "tasks": tasks,
            "updatedAt": time.time(),
            "ready": False,
            "context": {
                "appVersion": SUPPORTED_APP,
                "clientVersion": 58000,
                "configPath": str(config) + "/",
                "downloadPath": str(downloads) + "/",
                "peerId": uuid.uuid4().hex[:16].upper(),
                "guid": str(uuid.uuid4()),
                "networkType": 0,
            },
        }
        with path.open("x") as handle:
            os.chmod(path, 0o600)
            json.dump(job, handle)
        try:
            with (directory / "host.log").open("x") as log:
                os.chmod(log.name, 0o600)
                process = subprocess.Popen(
                    [str(executable), str(path)],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
        except OSError:
            raise XLCLIError("无法启动迅雷后台下载服务") from None
        deadline = time.monotonic() + 40 + 16 * len(tasks)
        while time.monotonic() < deadline:
            job = self._load(path)
            if job.get("ready"):
                result = [self._task(task) for task in job["tasks"]]
                if all(task.status == "失败" for task in result):
                    raise XLCLIError(result[0].message)
                return result
            if process.poll() is not None:
                raise XLCLIError("迅雷后台服务提前退出，任务未确认")
            time.sleep(0.1)
        process.terminate()
        raise XLCLIError("迅雷后台服务超时，任务未确认")

    @staticmethod
    def _task(task: dict) -> OfflineTask:
        return OfflineTask(
            task["id"],
            task["name"],
            task["status"],
            task["progress"],
            message=task.get("message", "") or task["directory"],
        )

    def tasks(self, limit: int = 20) -> list[OfflineTask]:
        if limit <= 0:
            raise XLCLIError("任务数量必须大于零")
        paths = sorted(
            (self.root / "jobs").glob("xl-*/job.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return [
            self._task(task) for path in paths for task in self._load(path)["tasks"]
        ][:limit]

    def wait(self, task_id: str, timeout: int = 3600) -> OfflineTask:
        prefix, separator, number = task_id.partition(":")
        if (
            not separator
            or len(prefix) != 15
            or not prefix.startswith("xl-")
            or any(c not in "0123456789abcdef" for c in prefix[3:])
            or not number.isdigit()
        ):
            raise XLCLIError("本机任务编号无效，请从 xl tasks 复制")
        if timeout <= 0:
            raise XLCLIError("等待时间必须大于零")
        path = self.root / "jobs" / prefix / "job.json"
        deadline = time.monotonic() + timeout
        while True:
            matches = [t for t in self._load(path)["tasks"] if t["id"] == task_id]
            if not matches:
                raise XLCLIError("没有找到这个本机任务")
            task = self._task(matches[0])
            if task.status in {"已完成", "失败"}:
                return task
            if time.monotonic() >= deadline:
                raise XLCLIError("等待超时，下载继续在后台运行；使用 xl tasks 查看")
            time.sleep(0.5)
