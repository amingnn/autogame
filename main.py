"""AutoGame 启动入口。"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import uvicorn

from core.common import Config
from core.config_store import 迁移旧版配置
from core.logger import configure_logging, mlog
from core.scheduler import Scheduler
from desktop import run_desktop_app
from webhook import create_app


def _解析启动参数() -> argparse.Namespace:
    """解析唯一的自动化启动参数。"""

    parser = argparse.ArgumentParser(description="AutoGame 自动化任务程序")
    parser.add_argument(
        "--automation",
        action="store_true",
        help="以无界面自动化模式运行",
    )
    return parser.parse_args()


async def _运行自动化(config: Config) -> None:
    """启动无界面 FastAPI 服务和任务调度器。"""

    scheduler = Scheduler(config, auto_shutdown=True, auto_schedule=True)
    stop_event = asyncio.Event()
    scheduler.bind_stop_event(stop_event)
    app = create_app(scheduler)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="0.0.0.0",
            port=config.system.webhook_port,
            log_level=config.system.log_level.lower(),
            access_log=False,
            loop="none",
        )
    )

    server_task = asyncio.create_task(server.serve())
    poll_task = asyncio.create_task(scheduler.poll_loop())
    watchdog_task = asyncio.create_task(scheduler.timeout_watchdog())
    stop_task = asyncio.create_task(stop_event.wait())

    done, _ = await asyncio.wait(
        {server_task, stop_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    if server_task in done and not server_task.cancelled():
        exception = server_task.exception()
        if exception:
            raise exception
    if stop_task in done:
        mlog.info("自动化任务已结束，正在停止服务")
    server.should_exit = True

    for task in (poll_task, watchdog_task):
        task.cancel()
    if not stop_task.done():
        stop_task.cancel()
    if not server_task.done():
        try:
            await asyncio.wait_for(server_task, timeout=10)
        except asyncio.TimeoutError:
            server_task.cancel()
    await asyncio.gather(
        server_task,
        poll_task,
        watchdog_task,
        stop_task,
        return_exceptions=True,
    )


def main() -> None:
    """根据启动参数进入桌面模式或自动化模式。"""

    arguments = _解析启动参数()
    config_path = Path(__file__).resolve().parent / "config.yaml"
    迁移旧版配置(config_path)
    config = Config.load(config_path)
    configure_logging(config.log_dir, config.system.log_level, force=True)
    mlog.info("配置加载完成，Webhook 端口：{}", config.system.webhook_port)

    if arguments.automation:
        asyncio.run(_运行自动化(config))
    else:
        run_desktop_app(config)


if __name__ == "__main__":
    main()
