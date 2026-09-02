# xl — 本机迅雷与云盘融合 CLI

> 非官方开源项目，与迅雷网络技术有限公司无隶属或背书关系。请仅下载你有权获取的内容，
> 并遵守所在地法律、服务条款和网络提供商政策。

`xl` 提供统一入口，后端可以是本机官方迅雷或迅雷云盘。默认 `auto` 优先使用本机
迅雷，因此无需登录；只有明确选择云盘后端时才需要账号。

本机后端通过 macOS LaunchServices 把磁力链接、ED2K、thunder 链接、普通
HTTP(S) 地址或 `.torrent` 文件交给 `/Applications/Thunder.app`。它使用的就是
本机官方迅雷引擎和当前未登录状态，不需要 `xl` 账号，也不会读取或提取迅雷登录信息。

```bash
# 两种写法等价，不需要登录
xl 'magnet:?xt=urn:btih:...'
xl add 'ed2k://...'

# 本地种子
xl ~/Downloads/example.torrent

# 提交任务但不把迅雷切到前台
xl add --background 'magnet:?xt=urn:btih:...'

# 查看官方客户端版本及进程状态
xl status
```

这种模式的下载行为和速度就是本机官方迅雷当前能提供的效果。`xl` 只负责提交任务；
进度、暂停、文件选择和保存目录仍由迅雷客户端管理。

## 融合后端

相同的 `add` 和 `get` 命令可以通过 `--engine` 选择后端：

```bash
# auto：默认；有本机迅雷就用本机，否则尝试已登录的云盘
xl add 'magnet:?xt=urn:btih:...' --engine auto

# local：强制交给官方 Mac 迅雷，不需要登录
xl get 'magnet:?xt=urn:btih:...' --engine local

# cloud：迅雷云盘离线后由 CLI 下载到指定目录，需要先登录
xl login
xl get 'magnet:?xt=urn:btih:...' ~/Downloads --engine cloud
```

云盘的任务和文件管理也使用普通命令：`xl tasks`、`xl wait`、`xl files`、
`xl download` 和 `xl logout`。`xl status` 会同时显示两个后端，并说明自动模式会选谁。

## 安全默认值

- 账号密码只在交互登录期间存在于内存，不写入文件。
- access token 和 refresh token 保存在 macOS 登录钥匙串。
- 设置文件只保存稳定设备 ID 和非敏感用户名，权限为 `0600`。
- 登录和云盘 API 只允许访问硬编码的迅雷官方 HTTPS 域名。
- 不提供删除命令，`get` 下载完成后也不会删除云盘文件。
- 默认拒绝覆盖本机已有文件；只有显式传入 `--force` 才允许覆盖。
- API 调用强制限频；下载默认按大小使用 1、2 或最多 4 个连接。
- 分段下载严格检查 HTTP 206 和 `Content-Range`，完成后再原子改名。

## 安装

要求：macOS、Python 3.11 或更高版本。本机后端需要安装官方 Mac 迅雷。

```bash
git clone https://github.com/Zichao-xu/xunlei-safe-cli.git
cd xunlei-safe-cli
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/xl status
```

若使用 `pipx`：

```bash
pipx install git+https://github.com/Zichao-xu/xunlei-safe-cli.git
xl status
```

## 已知边界

本机后端等同于用 Finder 或浏览器把任务交给官方迅雷，不是无界面的独立下载内核，
因此 CLI 暂时不能查询迅雷客户端内部进度。云盘后端使用非公开接口，可能随迅雷更新而变化。

## 开发

```bash
python3 -m venv .venv
.venv/bin/pip install -e . ruff
.venv/bin/ruff format --check src tests
.venv/bin/ruff check src tests
.venv/bin/python -m unittest discover -s tests -v
```

项目使用 [MIT License](LICENSE)。安全问题请参阅 [SECURITY.md](SECURITY.md)。
