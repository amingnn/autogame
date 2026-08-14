# AutoGame 最终重构计划与实施结果

## 目标

- `python main.py` 只启动桌面，不自动运行任务；
- `python main.py --automation` 运行一次无界面自动化会话；
- 桌面和自动化只共享任务核心，彼此不导入；
- 删除 FastAPI、Uvicorn、Webhook 和本地端口；
- 任务实现统一归入 `autogame/tasks/`；
- 运行数据移出源码包；
- 防止桌面、计划任务和重复自动化进程并发执行同一任务；
- 保持 Loguru 七日日志和安全配置保存。

## 已完成

### 1. 任务目录收敛

- 删除顶层 `tasks/` 和独立 `adapters/`；
- 通用任务协议迁入 `autogame/tasks/base.py`；
- 外部脚本生命周期迁入 `autogame/tasks/process_script.py`；
- MAA、MaaEnd 和森空岛全部由 `registry.py` 注册；
- 森空岛拆分为适配器、业务服务、HTTP 客户端、Token 存储和签名实现。

### 2. 桌面和自动化解耦

- 原 `Scheduler` 拆分为 `TaskManager` 和 `AutomationRunner`；
- `main.py` 根据参数延迟导入入口模块；
- 自动化模式不导入 pywebview；
- 桌面模式不创建自动扫描器或电源控制器；
- pywebview 页面通过 `DesktopBridge` 直接调用 Python。

### 3. 删除本地 Web 服务

- 删除 `autogame/api.py`；
- 移除 FastAPI、Uvicorn 和 httpx 依赖；
- 删除所有管理 HTTP 接口；
- 删除 `webhook_port` 配置；
- 页面不再使用 `fetch()`；
- 不再占用本地端口。

### 4. 运行能力拆分

新增 `autogame/runtime/`：

- `process.py`：启动、验证和停止 Windows 进程；
- `log_reader.py`：增量读取脚本日志；
- `state_store.py`：加锁并原子保存成功时间；
- `execution_lock.py`：自动化实例锁和任务锁；
- `power.py`：异步执行完成后的电源策略。

### 5. 外部脚本流程

MAA：

```text
启动/复用 MuMu -> 新启动时等待 20 秒 -> 启动 MAA
-> 读取 gui.log -> 清洗任务和专精日志 -> 检测整轮完成标记
```

MaaEnd：

```text
启动/复用终末地 -> 新启动时等待 20 秒 -> 启动 MaaEnd
-> 读取 maafw/go-service 日志 -> 检测结束进程任务或日志静默
```

游戏进程允许复用；脚本进程发现同路径旧实例时先关闭再重启。所有到期任务并发启动，全部成功后才执行完成动作。

### 6. 配置和运行数据

- 正式配置只删除已失去用途的 `webhook_port`；
- 其他当前电脑配置值保持不变；
- `config.yaml.bak` 保留迁移前配置；
- 配置保存继续使用文件锁、版本检查、注释保留、备份和原子替换；
- `state.json` 迁移到 `data/state.json`；
- 森空岛 Token 迁移到 `data/skyland_sign/token.txt`；
- 锁文件统一放入 `data/locks/`。

### 7. 依赖

基础自动化依赖不包含桌面库。pywebview 使用 `desktop` 可选依赖：

```powershell
uv sync --extra desktop
```

自动化环境只需：

```powershell
uv sync
```

### 8. Windows 计划任务

计划任务执行入口保持：

```powershell
uv run python main.py --automation
```

维护脚本对已有任务只更新执行动作，保留电脑现有触发器、账户和其他设置；创建新任务时使用 07:00 和 19:00 两个时间点。

## 验收结果

- 桌面后台启动不会调用任务启动器；
- 自动化入口不会导入桌面包；
- 页面不包含 HTTP 请求；
- 配置不再接受 `webhook_port`；
- SendKey 不会出现在桌面状态结果；
- 状态文件可原子合并多个任务成功时间；
- 同名跨进程任务锁能阻止重复运行；
- MAA 完成标记和 MaaEnd 业务日志解析正常；
- MAA 任务错误只记录为 `【ERR】` INFO 日志，不影响整轮完成；
- MaaEnd 支持结束进程标志和十分钟日志静默兜底；
- 到期任务并发启动，失败或超时时不会执行系统完成动作；
- Loguru 会清理七天以前的日志；
- 当前自动化测试全部通过。

具体目录、运行流程、类和公共函数以 [架构文档](architecture.md) 为准。
