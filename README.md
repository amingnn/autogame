# Auto Game

轻量级、配置驱动的游戏自动化框架。通过 Webhook 回调与定时轮询双触发机制，实现定时唤醒、自动运行、全部完成后自动睡眠的无人值守流程。

## 免责声明

本工具仅供学习交流使用，请勿用于商业用途或违反游戏服务条款的行为。使用本工具导致的任何后果由使用者自行承担，作者不承担任何责任。

---

## 快速开始

### 1. 安装依赖

```bash
uv sync
```

### 2. 初始化配置

```bash
cp config.example.yaml config.yaml
```

按需编辑 `config.yaml`，重点填写：

| 字段 | 说明 |
|---|---|
| `system.webhook_port` | Webhook 监听端口，需与 MAA 等外部程序配置一致 |
| `system.server_chan_key` | [Server 酱](https://sct.ftqq.com) SendKey，留空则不推送 |
| `tasks.*.entry` | 任务入口函数路径（`tasks/` 内的 `module.function`） |
| `tasks.*.done_on` | 完成判定方式：`entry`（函数返回即完成）或 `webhook`（等待回调） |

### 3. 配置森空岛 Token

首次运行时需要录入账号：

```bash
uv run python tasks/skyland_sign/skyland.py
```

按提示登录后 Token 保存在 `tasks/skyland_sign/TOKEN.txt`，后续无需重复操作。

### 4. 运行

```bash
uv run python main.py
```

建议通过 Windows 计划任务定时运行，脚本会自动完成所有任务后睡眠。

---

## 运行流程

```
计划任务唤醒/定时触发 → main.py 启动
├── Webhook 服务开始监听（:8000）
└── 轮询首次扫描
    ├── skyland_sign → 调用 skyland_sign.skyland.start() → 返回即完成（done_on: entry）
    ├── maa          → 调用 maa.run() 启动 MAA → 等待 POST /maa
    └── maaend       → 调用 maaend.run() 启动 MAAEnd.exe → 等待 GET /maa

MAA 跑完  → POST /maa       → maa 完成
终末地跑完 → GET  /maa?msg= → maaend 完成

全部完成 → notify 报告写入 logs/notify-*.log → Server酱推送 → sleep
```

---

## 日志结构

```
logs/
├── 2026-03-21.log       # 主日志（调度、任务开始/结束、系统事件）
└── notify-2026-03-21.log  # 通知日志（任务业务结果，用于 Server酱 推送）
```

主日志仅记录 `[notify] 报告已写入 → notify-*.log` 指针，通知内容不在控制台输出。

---

## Webhook 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/done` | 任务自报完成，Body: `{"task": "<name>"}` |
| `POST` | `/maa` | MAA 任务完成回调（含日志 content） |
| `GET` | `/maa` | 终末地完成回调（?msg=...） |
| `POST` | `/trigger` | 手动触发任务，Body: `{"trigger": "<name>", "force": true}` |

---

## 新增任务

1. 在 `tasks/` 下新建目录，例如 `tasks/my_task/`
2. 创建 `tasks/my_task/__init__.py`（空文件）
3. 实现入口函数（文件名任意，不再强制要求 `client.py`）：

```python
# tasks/my_task/runner.py
def start():
    do_something()
```

4. 在 `config.yaml` 注册任务：

```yaml
tasks:
  my_task:
    enabled: true
    interval_hours: 24
    entry: "my_task.runner.start"   # tasks/ 内 module.function 路径
    start_on: "entry"               # entry 执行时记录开始
    done_on: "entry"                # entry 返回即完成
    # done_on: "webhook"            # 如需等待外部回调，改为 webhook
```

5. 如果任务需要 webhook 完成通知，在业务代码中发送对应请求，或在 `webhook/listener.py` 中添加专用路由。

---

## 配置说明

```yaml
system:
  log_level: "INFO"
  webhook_port: 8000
  shutdown_on_complete: true      # 全部完成后执行 completion_action
  shutdown_delay_seconds: 60      # 执行动作前等待秒数
  shutdown_timeout_hours: 1.5     # 超时强制执行完成动作（小时）
  completion_action: "sleep"      # sleep / shutdown / none
  poll_interval_hours: 2.0        # 轮询间隔（测试时可设为 0.003 ≈ 10秒）
  server_chan_key: ""              # Server酱 SendKey，留空不推送

tasks:
  skyland_sign:
    enabled: true
    interval_hours: 20
    entry: "skyland_sign.skyland.start"  # tasks/ 内的 module.function
    start_on: "entry"    # entry 函数调用时记录开始；"run" = 轮询触发时记录
    done_on: "entry"     # entry 返回即完成；"webhook" = 等待 webhook 回调

  maa:
    enabled: true
    interval_hours: 10
    entry: "maa.run"     # 启动 MAA
    start_on: "entry"
    done_on: "webhook"

  maaend:
    enabled: true
    interval_hours: 10
    entry: "maaend.client.run"
    start_on: "entry"
    done_on: "webhook"
```

## License

MIT License
