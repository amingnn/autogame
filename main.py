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

    await asyncio.gather(
        server.serve(),
        scheduler.poll_loop(),
    )


if __name__ == "__main__":
    asyncio.run(main())
