import asyncio
import uvicorn

from core.common import Config
from core.logger import mlog
from core.scheduler import Scheduler
from webhook import create_app


async def main() -> None:
    config = Config.load()
    mlog.info(f"配置加载完成，Webhook 端口: {config.system.webhook_port}")

    scheduler = Scheduler(config)
    stop_event = asyncio.Event()
    scheduler.bind_stop_event(stop_event)
    app = create_app(scheduler)

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="0.0.0.0",
            port=config.system.webhook_port,
            log_level=config.system.log_level.lower(),
            # 在 asyncio.gather 中运行，不能独占事件循环
            loop="none",
        )
    )

    server_task = asyncio.create_task(server.serve())
    poll_task = asyncio.create_task(scheduler.poll_loop())
    watchdog_task = asyncio.create_task(scheduler.timeout_watchdog())
    stop_task = asyncio.create_task(stop_event.wait())

    done, pending = await asyncio.wait(
        {server_task, watchdog_task, stop_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    if stop_task in done:
        mlog.info("调度器请求退出，正在停止 Webhook 服务...")
        server.should_exit = True

    for task in pending:
        task.cancel()

    await asyncio.gather(
        server_task,
        poll_task,
        watchdog_task,
        stop_task,
        return_exceptions=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
