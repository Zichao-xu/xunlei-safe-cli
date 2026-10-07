# xl — 本机迅雷与云盘融合 CLI

非官方开源项目，与迅雷无隶属或背书关系。

`xl` 默认直接调用已安装的官方 Mac 迅雷下载内核，在后台创建并启动任务。
无需无障碍权限，不点击按钮、不切换窗口、不模拟键盘，也不读取客户端登录信息。
它使用迅雷自带的 `DownloadService.xpc`，没有接入 aria2 或其他下载引擎。

```bash
# 立即启动后台下载，终端退出后继续下载
xl 'https://example.org/file.zip'
xl add ~/Downloads/example.torrent
xl add 'magnet:?xt=urn:btih:...'

# 启动并等待完成；output 是目录，会创建独立下载子目录
xl get 'https://example.org/file.zip' ~/Downloads --timeout 3600

# 查询 xl 创建的任务；复制任务编号等待完成
xl tasks
xl wait 'xl-0123456789ab:1'
xl status
```

本机任务默认保存到 `~/Downloads/xl/日期-批次/序号/`。每次提交使用独立目录，
避免覆盖已有文件；种子默认下载全部文件。`--background` 保留为兼容参数，本机模式始终后台运行。
`get` 只有完成后返回成功；失败、等待超时均返回非零状态。等待超时或中断终端等待，后台下载继续。

## 原生后台如何工作

首次使用时，从 `/Applications/Thunder.app` **在本机复制**官方原生下载服务，
编译本项目的 Foundation/XPC 调用程序，组成 `LSBackgroundOnly` 后台程序。
服务仍保留原始签名。GitHub 和 Python 安装包不包含或分发迅雷二进制文件。

配置、任务数据库和下载目录与迅雷主程序隔离。后台任务不出现在迅雷主界面，
用 `xl tasks` 查询；主程序现有任务和设置不参与这条链路。每批任务完成或失败后，
后台程序退出。任务记录保存在 `~/Library/Application Support/xlcli/native/`，
包含源地址和文件路径，目录仅当前用户可访问。

接口属于迅雷私有协议，当前只接受已验证的迅雷 **5.80.0**、下载服务 **8.705.460**、
内核 `11.0114.460.24 - 11.0114.24r`。版本不匹配会明确失败，
不会自动弹出主界面、调用无障碍或换成其他引擎。

## 实测范围和边界

2026-10-07，在 macOS 的真实官方内核上验收全部 6 类入口：

| 入口 | 结果 |
| --- | --- |
| HTTP | 完成下载，文件内容一致 |
| HTTPS | 完成下载，文件内容一致 |
| thunder 链接 | 解码后由原生内核完成下载，内容一致 |
| 本地 .torrent | 本地 tracker/peer 提供数据，原生内核完成文件下载，内容一致 |
| magnet | 已创建原生种子获取任务；自建测试资源未取得元数据，端到端完成尚未验证 |
| ED2K | 原生任务已创建，但测试下载被内核报错；完成下载尚未验证 |

磁力任务取得种子后会自动创建 BT 文件下载；取得种子本身不会被报告为文件下载完成。
磁力衔接尚需可用资源进行完整实测。独立内核没有复用客户端登录或会员凭据，
不保证会员专有加速。ED2K 的当前内核可用性仍待验证。

当前不提供暂停、重启续传或系统开机自启。电脑重启、下载服务退出后，
超过 45 秒没有状态更新的未完成任务会显示“已中断”，需要重新添加。
本机 `get` 的 output 必须是目录，不支持 `--force` 和 `--connections`。

## 云盘后端

本机和云盘均通过 `--engine auto|local|cloud` 选择。
`auto` 优先本机；没有本机迅雷时才尝试已经登录的云盘。
本机版本不兼容或下载失败时不会切换到云盘。

```bash
xl login
xl get 'magnet:?xt=urn:btih:...' ~/Downloads --engine cloud
xl tasks --engine cloud
xl wait '云盘任务编号' --engine cloud
xl files
xl download '云盘文件编号' ~/Downloads
xl logout
```

`wait` 的自动模式会按任务编号识别本机和云盘，保留已有云盘等待方式。
云盘密码只在登录期间存在于内存，令牌保存在 macOS 登录钥匙串；API 限频并限制到
迅雷官方 HTTPS 域名。云盘分段下载检查 `Content-Range`，默认拒绝覆盖已有文件。
云盘使用非公开接口，也可能随迅雷更新变化。

## 安装和验证

要求：macOS、Python 3.11+、官方 Mac 迅雷 5.80.0，以及 Apple 命令行工具（提供 clang）。

```bash
git clone https://github.com/Zichao-xu/xunlei-safe-cli.git
cd xunlei-safe-cli
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/xl status
```

亦可 `pipx install git+https://github.com/Zichao-xu/xunlei-safe-cli.git`。

```bash
.venv/bin/pip install -e . ruff
.venv/bin/ruff format --check src tests
.venv/bin/ruff check src tests
.venv/bin/python -m unittest discover -s tests -v
xcrun clang -fobjc-arc -Wall -Werror -framework Foundation src/xlcli/native/host.m -o /tmp/xl-native-host
# 已安装兼容迅雷的 Mac：真实后台 HTTP 下载与内容校验
.venv/bin/python tools/check_native.py
```

CI 覆盖 Python 回归测试和原生调用程序编译；CI 没有安装迅雷，不能代替原生下载验收。
项目使用 [MIT License](LICENSE)。安全问题请参阅 [SECURITY.md](SECURITY.md)。
