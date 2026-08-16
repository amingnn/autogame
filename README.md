# AutoGame

AutoGame 是一个配置驱动的 Windows 游戏自动化任务管理器，提供 pywebview 桌面管理和无界面计划任务模式。

## 安装

安装全部运行依赖：

```powershell
uv sync
```

## 启动

桌面模式：

```powershell
uv run python main.py
```

桌面模式只打开管理窗口，不扫描或自动启动任务。任务必须由页面手动运行。

无界面自动化模式：

```powershell
uv run python main.py -a
```

强制运行所有已启用任务、忽略冷却时间（等价于桌面端“全部运行”）：

```powershell
uv run python main.py -a -f
```

`-f` 也可以写成 `--force`；禁用的任务仍不会运行。

自动化模式只扫描本次启动时已经达到 `interval_hours` 的任务，并发启动后等待全部任务结束。有到期任务时，完成动作会按配置强制执行，不受任务失败或超时影响；没有到期任务或动作配置为 `none` 时不执行。它不会导入 pywebview、读取页面、启动本地服务或占用端口。

## 配置

正式配置为根目录的 `config.yaml`，完整模板为 `examples/config.example.yaml`，MAA 专用示例为 `examples/maa-config.example.yaml`。桌面保存配置时会保留 YAML 注释、创建 `.bak`、检查版本冲突并原子替换文件。

任务配置示例（完整内容可直接参考 `examples/config.example.yaml`）：

```yaml
tasks:
  skyland_sign:
    enabled: true
    interval_hours: 20

  maa:
    enabled: true
    interval_hours: 3
    script_path: "D:\\game\\MAA-v6.13.0-win-x64\\MAA.exe"

  maaend:
    enabled: true
    interval_hours: 20
    script_path: "D:\\game\\MaaEnd-win-x86_64-v2.18.0\\MaaEnd.exe"
```

MAA 和 MaaEnd 只需配置脚本 exe。两者都只启动、监控和重启配置的脚本进程，不负责启动游戏；MAA 读取 `debug/gui.log`，MaaEnd 读取 `debug/maafw*.log` 和 `debug/go-service.log`。当前版本对应的 MAA 配置也单独放在 `examples/maa-config.example.yaml` 中。

自动化策略示例：

```yaml
system:
  automation_timeout_minutes: 30
  completion_action: "hibernate"
  completion_action_delay_seconds: 60
  server_chan_enabled: true
```

`completion_action: "none"` 表示完成后不执行系统动作。
`server_chan_enabled: false` 表示不发送 Server 酱通知。

## 任务流程

- `skyland_sign`：在项目进程内调用森空岛接口；
- `maa`：启动配置的 MAA，只读取 `debug/gui.log` 中的任务和专精信息；游戏由外部方式启动；
- `maaend`：启动配置的 MaaEnd，监听 `debug/maafw*.log` 和 `debug/go-service.log`；终末地由外部方式启动。

外部脚本已有同路径旧实例时会先安全关闭再重新启动。MAA 读取到“任务已全部完成”后完成；“任务出错”只作为 `【ERR】` 业务日志展示。MaaEnd 以“结束进程”任务为完成标志，并保留十分钟有效业务日志静默兜底。不再使用 Webhook。

任务状态：

```text
disabled / cooldown / pending / starting
running / completed / failed / timed_out
```

## 桌面通信

页面通过 `window.pywebview.api` 直接调用 `DesktopBridge`，项目不再包含 FastAPI、Uvicorn、本地 HTTP API 或端口配置。

桌面可以执行：

- 查看任务状态和近期日志；
- 手动运行或强制运行任务；
- 修改任务启用状态、间隔和脚本路径；
- 修改日志、通知和自动化完成策略；
- 重载根目录的 `config.yaml`。

## 运行数据

```text
data/maa/config/                 # 从 MAA 实际安装目录迁移的配置和自定义脚本
data/maaend/config/              # 从 MaaEnd 实际安装目录迁移的配置
data/state.json                  # 任务最近成功时间
data/skyland_sign/token.txt      # 森空岛 Token
data/locks/                      # 自动化实例锁和任务执行锁
logs/YYYY-MM-DD.log              # 项目主日志
logs/notify-YYYY-MM-DD.log       # 通知日志
```

森空岛签到优先使用 `data/skyland_sign/token.txt` 中的缓存 Token，只有 Token 失效时才使用 `tasks.skyland_sign.account.phone/password` 刷新并更新缓存；没有缓存或账号配置为空时会报告失败。手机号和密码只应填写在本机根目录 `config.yaml`，不要放入示例文件或提交版本库。首次使用时，复制 `examples/config.example.yaml` 为根目录 `config.yaml`，再填写本机路径、通知密钥和森空岛账号。

Loguru 日志按日轮转，启动时和轮转时都会清理七天以前的日志。

## Windows 计划任务

```powershell
.\scripts\register-autogame-task.ps1
```

已有 AutoGame 计划任务只更新执行入口，保留当前电脑的触发器、账户、运行级别和其他设置；新电脑默认创建 07:00 和 19:00 两个触发时间。MAA 和 MaaEnd 任务都不负责启动游戏，也不改变计划任务权限。

## 文档

- [实施计划和结果](docs/plan.md)
- [项目架构说明](docs/architecture.md)

## 免责声明

本项目仅供学习和个人自动化研究使用。请遵守相关游戏服务条款和法律法规，因使用本工具产生的后果由使用者自行承担。
