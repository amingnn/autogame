# Auto Game

游戏自动启动与任务管理工具。

## 免责声明

本工具仅供学习交流使用，请勿用于商业用途或违反游戏服务条款的行为。使用本工具导致的任何后果由使用者自行承担，作者不承担任何责任。

## 使用方法

### 环境要求

- Python 3.10+
- uv (推荐)

### 安装

```bash
uv sync
```

### 配置

编辑 `config.yaml`：

```yaml
system:
  log_level: "INFO"           # 日志级别
  webhook_port: 8080         # Webhook 端口
  shutdown_on_complete: true # 任务完成后是否自动关闭
  shutdown_delay_seconds: 60 # 关闭延迟（秒）
  shutdown_timeout_hours: 1.0 # 超时自动关闭（小时）
  server_chan_key: ""        # Server酱 SendKey

tasks:
  skyland_sign:
    enabled: true
    interval_hours: 10
  maa:
    enabled: true
    interval_hours: 1
  maaend:
    enabled: true
    interval_hours: 1
```

### 运行

```bash
uv run python main.py
```

### Webhook

- `POST /maa` - MAA 任务完成通知
- `GET /maa` - MAA 状态查询

### 新增任务

把已有任务或新开发的任务放到tasks下，并需要有一个client.py里面需要有run函数
在config配置新任务的配置

## License

MIT License