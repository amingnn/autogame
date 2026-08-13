# AutoGame 任务生命周期重构实施计划

## 目标

本次重构解决桌面启动误执行、任务状态不清晰、配置无法通过页面管理以及入口配置过于自由的问题。

最终运行方式：

```powershell
# 桌面管理：只启动桌面，不自动启动任务
python main.py

# 自动化运行：启动调度器并按 interval_hours 扫描到期任务
python main.py --automation
```

本版本继续使用间隔小时调度，暂不实现每天次数调度。

## 已实施改动

### 启动模式

- `desktop/app.py` 创建的 Scheduler 使用 `auto_schedule=False`；
- 桌面后端只运行本地 FastAPI 和 pywebview，不启动轮询和超时监控任务；
- `main.py --automation` 使用 `auto_schedule=True`，调度器每 30 秒扫描一次到期任务；
- 页面点击运行和 `/trigger` 仍然可以手动启动任务。

### 任务模型

任务配置删除 `entry`、`start_on`、`done_on`，改用 `launcher`：

- `type: none` 表示无外部应用；
- `type: application` 表示启动 `.exe` 或 `.lnk`，并验证目标进程名；
- `core/task_registry.py` 固定任务业务行为和完成信号，避免从 YAML 动态导入任意函数。

### 状态模型

状态为：

```text
disabled / cooldown / pending / starting / running
completed / failed / timed_out
```

MAA 等待 Webhook 时仍保持 `running`，通过 `waiting_for_callback` 和页面说明显示正在等待回调。

### 配置管理

页面可以保存任务配置和全部可运行的全局配置。配置保存具备：

- 文件锁；
- SHA-256 版本冲突检测；
- YAML 注释保留；
- `.bak` 备份；
- 临时文件原子替换；
- Pydantic 校验后重新加载。

旧配置启动时由 `迁移旧版配置()` 自动转换，无法识别的未知入口会拒绝迁移并保留原文件。

### 日志

统一使用 Loguru，主日志和通知日志按天轮转，并通过 `retention="7 days"` 与启动清理共同保证只保留最近七天。

## 验收结果

当前测试覆盖：

- 内置任务 `starting -> running -> completed` 的完成路径；
- 应用启动后等待回调，回调完成和重复回调忽略；
- 桌面 Scheduler 不执行自动扫描；
- YAML 注释、备份、配置迁移；
- 任务配置、全局配置、版本冲突 API；
- SendKey 不出现在状态接口中；
- 正式 `config.yaml` 使用新启动器模型加载成功；
- Python 编译检查和主入口 `--help` 检查。
