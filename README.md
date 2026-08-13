# AutoGame

AutoGame 是一个配置驱动的 Windows 游戏自动化任务程序，默认提供 pywebview 桌面管理窗口，同时保留无界面自动化运行能力。

## 启动

普通用户运行桌面模式：

```powershell
uv run python main.py
```

桌面模式只启动窗口、状态服务和 Webhook 接收服务，不会自动启动任何脚本。任务必须由页面点击“运行”，或者由外部 `/trigger` 请求触发。

自动化脚本运行：

```powershell
uv run python main.py --automation
```

自动化模式按任务的 `interval_hours` 周期扫描到期任务，不打开桌面窗口。任务全部完成后按照全局配置执行退出、睡眠、休眠或关机动作。

## 安装

```powershell
uv sync
```

首次使用森空岛签到任务时，可以手动运行：

```powershell
uv run python tasks/skyland_sign/skyland.py
```

登录生成的 Token 只保存在本地任务目录，不提交到版本库。

## 配置

正式配置文件为 `config.yaml`，模板为 `config.example.yaml`。页面保存会保留 YAML 注释，并使用文件锁、版本校验、`.bak` 备份和原子替换。

全局配置包括：

- 日志级别、Webhook 端口；
- 自动化超时时间；
- 任务完成后的电源动作和延迟；
- Server 酱 SendKey。页面不会回显 SendKey，留空表示保持原值，勾选清除才会删除。

任务配置包括：

```yaml
tasks:
  skyland_sign:
    enabled: true
    interval_hours: 20
    launcher:
      type: "none"
  maa:
    enabled: true
    interval_hours: 3
    launcher:
      type: "application"
      path: "D:\\OneDrive\\win\\桌面\\MAA.exe.lnk"
      process_name: "MAA.exe"
      startup_timeout_seconds: 15
```

不再使用 `entry`、`start_on`、`done_on`。旧配置启动时会自动备份并迁移；未知 Python 入口不会被自动执行。

## 任务模型

- `skyland_sign`：无外部应用，运行内置签到函数，返回成功后完成；
- `maa`：启动并验证 `MAA.exe`，状态保持为运行中，收到 POST `/maa` 后完成；
- `maaend`：启动并验证 `MaaEnd.exe`，状态保持为运行中，收到 GET `/maa` 后完成。

任务状态为：`disabled`、`cooldown`、`pending`、`starting`、`running`、`completed`、`failed`、`timed_out`。

## 接口

外部兼容接口：

```text
POST /maa
GET  /maa
POST /trigger
```

本机桌面管理接口：

```text
GET   /api/status
POST  /api/tasks/{task_name}/run
PATCH /api/tasks/{task_name}
PATCH /api/config/system
POST  /api/config/reload
GET   /api/logs/recent
```

## 日志

日志由 Loguru 管理，按天生成：

```text
logs/YYYY-MM-DD.log
logs/notify-YYYY-MM-DD.log
```

启动时和 Loguru 轮转时均只保留最近七天日志。SendKey 不写入普通状态和日志内容。

## Windows 计划任务

```powershell
.\scripts\register-autogame-task.ps1
```

注册脚本对已有任务只更新执行入口为 `python main.py --automation`，保留电脑现有的两个运行时间和其他计划任务设置。

## 文档

- [实施计划](docs/plan.md)
- [项目架构说明](docs/architecture.md)

## 免责声明

本项目仅供学习和个人自动化研究使用。请遵守相关游戏服务条款和法律法规，因使用本工具产生的后果由使用者自行承担。
