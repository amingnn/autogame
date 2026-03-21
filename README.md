# Auto Game

轻量级、配置驱动的游戏自动化框架。通过 Webhook 回调与定时轮询双触发机制，实现开机自启、自动运行、全部完成后自动关机的无人值守流程。

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
| `tasks.*.enabled` | 各任务开关 |
| `tasks.*.interval_hours` | 任务最小触发间隔（小时） |

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

建议配置为**开机自启**，脚本会自动完成所有任务后关机。

---

## 运行流程

```
开机自启 → main.py 启动
├── Webhook 服务开始监听（:8000）
└── 轮询首次扫描
    ├── skyland_sign → 签到 → POST /done 自报完成
    ├── maa          → no-op（MAA 已开机自启）→ 等待 POST /maa
    └── maaend       → 启动 MAAEnd.exe → 等待 GET /maa

MAA 跑完  → POST /maa  → maa 完成
终末地跑完 → GET  /maa  → maaend 完成

全部完成 → Server酱推送报告 → shutdown /s /t 60
```

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
3. 创建 `tasks/my_task/client.py`，实现 `run()` 函数：

```python
# 自完成示例（run 返回即视为完成）
def run():
    ...

# Webhook 通知完成示例（run 只触发动作，完成由外部 POST /done 通知）
async def run():
    start_something()  # 启动外部程序
```

4. 在 `config.yaml` 注册任务：

```yaml
tasks:
  my_task:
    enabled: true
    interval_hours: 24
    webhook_notify: false  # true = 需要外部 POST /done 才算完成
```

---

## 配置说明

```yaml
system:
  log_level: "INFO"
  webhook_port: 8000
  shutdown_on_complete: true      # 全部完成后自动关机
  shutdown_delay_seconds: 60      # 关机前等待秒数
  shutdown_timeout_hours: 1.0     # 超时强制关机（小时）
  poll_interval_hours: 2.0        # 轮询间隔（测试时可设为 0.003 ≈ 10秒）
  server_chan_key: ""              # Server酱 SendKey，留空不推送

tasks:
  skyland_sign:
    enabled: true
    interval_hours: 10
    webhook_notify: true
  maa:
    enabled: true
    interval_hours: 1
    webhook_notify: true
  maaend:
    enabled: true
    interval_hours: 1
    webhook_notify: true
```

## License

MIT License
