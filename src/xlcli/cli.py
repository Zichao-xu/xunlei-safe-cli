from __future__ import annotations

import argparse
import getpass
import sys
import time
import webbrowser
from pathlib import Path

from . import __version__
from .api import DriveAPI
from .auth import Auth
from .downloader import Downloader
from .errors import VerificationRequired, XLCLIError
from .local_thunder import LocalThunder
from .storage import TokenStore

ENGINES = ("auto", "local", "cloud")


def human_size(value: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="xl",
        description="统一调用本机官方迅雷和云盘后端；默认无需登录",
    )
    root.add_argument("--version", action="version", version=__version__)
    sub = root.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="添加下载；默认使用本机迅雷，无需登录")
    add.add_argument("sources", nargs="+", help="链接或 .torrent 文件")
    add.add_argument("--engine", choices=ENGINES, default="auto")
    add.add_argument("--background", action="store_true", help="本地模式不切到前台")

    get = sub.add_parser("get", help="下载链接；可选择本机或云盘后端")
    get.add_argument("url")
    get.add_argument("output", nargs="?", type=Path)
    get.add_argument("--engine", choices=ENGINES, default="auto")
    get.add_argument("--background", action="store_true", help="本地模式不切到前台")
    get.add_argument("--timeout", type=int, default=3600)
    get.add_argument(
        "--connections", type=int, choices=range(1, 5), default=0, metavar="1-4"
    )
    get.add_argument("--force", action="store_true", help="云盘模式允许覆盖已有文件")

    login = sub.add_parser("login", help="登录云盘后端；不影响本地免登录模式")
    login.add_argument("--username", help="手机号、邮箱或迅雷用户名")
    login.add_argument(
        "--refresh-token", action="store_true", help="改用隐藏输入导入刷新令牌"
    )

    sub.add_parser("logout", help="删除云盘令牌；不影响本机迅雷")
    sub.add_parser("status", help="查看本机迅雷和云盘凭据状态")

    tasks = sub.add_parser("tasks", help="列出云盘离线任务（需登录）")
    tasks.add_argument("--limit", type=int, default=20)

    wait = sub.add_parser("wait", help="等待云盘离线任务（需登录）")
    wait.add_argument("task_id")
    wait.add_argument("--timeout", type=int, default=3600)

    ls = sub.add_parser("files", help="列出迅雷云盘文件（需登录）")
    ls.add_argument("parent_id", nargs="?", default="")
    ls.add_argument("--limit", type=int, default=100)

    download = sub.add_parser("download", help="下载迅雷云盘文件（需登录）")
    download.add_argument("file_id")
    download.add_argument("output", nargs="?", type=Path)
    download.add_argument(
        "--connections", type=int, choices=range(1, 5), default=0, metavar="1-4"
    )
    download.add_argument("--verify-md5", action="store_true")
    download.add_argument("--force", action="store_true", help="允许覆盖已有目标文件")

    return root


def _login(auth: Auth, args: argparse.Namespace) -> None:
    if args.refresh_token:
        token = getpass.getpass("迅雷 refresh token（输入不会显示）：").strip()
        if not token:
            raise XLCLIError("refresh token 不能为空")
        auth.login_refresh_token(token)
        print("登录成功，令牌已保存到 macOS 钥匙串。")
        return

    username = args.username or auth.settings.username() or input("迅雷账号：").strip()
    if not username:
        raise XLCLIError("账号不能为空")
    password = getpass.getpass("迅雷密码（不会保存）：")
    if not password:
        raise XLCLIError("密码不能为空")
    try:
        auth.login_password(username, password)
    except VerificationRequired as verification:
        print("迅雷要求完成一次设备验证，正在浏览器中打开官方验证页。")
        print(verification.url)
        webbrowser.open(verification.url)
        input("完成验证后按回车继续；Ctrl-C 可取消：")
        if not verification.credit_key:
            raise XLCLIError("验证完成后仍缺少 credit key，请重新运行登录")
        auth.login_password(username, password, verification.credit_key)
    finally:
        password = ""  # Keep the plaintext lifetime as short as Python permits.
    print("登录成功，令牌已保存到 macOS 钥匙串；密码没有保存。")


def _print_task(task) -> None:
    progress = task.progress * 100 if task.progress <= 1 else task.progress
    suffix = f" · {task.message}" if task.message else ""
    print(f"{task.id}\t{task.status}\t{progress:.0f}%\t{task.name}{suffix}")


def _progress_printer():
    last = [0.0]

    def report(done: int, total: int, speed: float) -> None:
        now = time.monotonic()
        if now - last[0] < 0.4 and done < total:
            return
        last[0] = now
        percent = done / total * 100 if total else 0
        print(
            f"\r{percent:6.2f}%  {human_size(done)} / {human_size(total)}  "
            f"{human_size(speed)}/s",
            end="",
            flush=True,
        )

    return report


def _select_engine(requested: str) -> str:
    if requested != "auto":
        return requested
    if LocalThunder().status().installed:
        return "local"
    try:
        if TokenStore().load() is not None:
            return "cloud"
    except XLCLIError:
        pass
    raise XLCLIError("既未找到本机官方迅雷，也没有可用的云盘登录")


def run(argv: list[str] | None = None) -> int:
    actual_argv = list(sys.argv[1:] if argv is None else argv)
    commands = {
        "add",
        "get",
        "login",
        "logout",
        "status",
        "tasks",
        "wait",
        "files",
        "download",
    }
    if (
        actual_argv
        and not actual_argv[0].startswith("-")
        and actual_argv[0] not in commands
    ):
        actual_argv.insert(0, "add")
    args = parser().parse_args(actual_argv)
    auth: Auth | None = None
    api: DriveAPI | None = None
    try:
        if args.command == "add":
            engine = _select_engine(args.engine)
            if engine == "local":
                submitted = LocalThunder().add(args.sources, args.background)
                print(f"已交给本机迅雷：{len(submitted)} 个任务（无需登录）")
                return 0
            if args.background:
                raise XLCLIError("--background 只适用于本机迅雷后端")
            auth = Auth()
            api = DriveAPI(auth)
            for source in args.sources:
                task = api.add_offline(source)
                _print_task(task)
            return 0
        if args.command == "get":
            engine = _select_engine(args.engine)
            if engine == "local":
                if args.output is not None:
                    raise XLCLIError(
                        "本机迅雷的保存目录由客户端管理，请不要指定 output"
                    )
                LocalThunder().add([args.url], args.background)
                print("已交给本机迅雷（无需登录）")
                return 0
            if args.background:
                raise XLCLIError("--background 只适用于本机迅雷后端")
            auth = Auth()
            api = DriveAPI(auth)
            task = api.add_offline(args.url)
            print(f"已创建云盘离线任务：{task.id}，等待完成……")
            task = api.wait_task(task.id, args.timeout)
            _print_task(task)
            if task.status != "已完成":
                return 2
            if not task.file_id:
                raise XLCLIError("任务完成但未返回文件 ID，请运行 xl files 查找")
            target = Downloader(api).download(
                task.file_id,
                args.output,
                args.connections,
                False,
                _progress_printer(),
                args.force,
            )
            print(f"\n完成：{target}")
            return 0
        if args.command == "status":
            local = LocalThunder().status()
            local_state = "未安装"
            if local.installed:
                local_state = f"{local.version or '未知版本'} · {'运行中' if local.running else '未运行'}"
            cloud_token = TokenStore().load()
            cloud_state = "已保存登录" if cloud_token is not None else "未登录"
            print(f"本机迅雷：{local_state}（无需登录）")
            print(f"云盘后端：{cloud_state}")
            print("自动选择：" + ("本机迅雷" if local.installed else "云盘后端"))
            return 0
        auth = Auth()
        if args.command == "login":
            _login(auth, args)
            return 0
        if args.command == "logout":
            auth.logout()
            print("云盘登录令牌已删除；本机迅雷不受影响。")
            return 0

        api = DriveAPI(auth)
        if args.command == "tasks":
            for task in api.tasks(args.limit):
                _print_task(task)
        elif args.command == "wait":
            task = api.wait_task(args.task_id, args.timeout)
            _print_task(task)
            return 0 if task.status == "已完成" else 2
        elif args.command == "files":
            for item in api.files(args.parent_id, args.limit):
                kind = "目录" if item.kind == "drive#folder" else human_size(item.size)
                print(f"{item.id}\t{kind}\t{item.name}")
        elif args.command == "download":
            downloader = Downloader(api)
            target = downloader.download(
                args.file_id,
                args.output,
                args.connections,
                args.verify_md5,
                _progress_printer(),
                args.force,
            )
            print(f"\n完成：{target}")
        return 0
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130
    except XLCLIError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    finally:
        if api is not None:
            api.close()
        if auth is not None:
            auth.close()


def main() -> None:
    raise SystemExit(run())
