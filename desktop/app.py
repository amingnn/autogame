"""启动 pywebview 桌面窗口和后台 FastAPI 服务。"""

from __future__ import annotations

import asyncio
import threading

import uvicorn

from core.common import Config
from core.logger import mlog
from core.scheduler import Scheduler
from webhook import create_app


class DesktopBackend:
    """在后台线程中运行 FastAPI 和 Scheduler。"""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.url = f"http://127.0.0.1:{config.system.webhook_port}"
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._error: BaseException | None = None

    def start(self) -> None:
        """启动后台服务并等待端口就绪。"""

        self._thread = threading.Thread(
            target=self._thread_main,
            name="autogame-backend",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=15):
            raise RuntimeError("后台服务启动超时")
        if self._error is not None:
            raise RuntimeError(f"后台服务启动失败：{self._error}") from self._error

    def stop(self) -> None:
        """请求后台服务停止，并等待线程退出。"""

        if self._loop is not None and self._stop_event is not None:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _thread_main(self) -> None:
        """在线程中运行异步服务。"""

        try:
            asyncio.run(self._run())
        except BaseException as exc:
            self._error = exc
            self._ready.set()

    async def _run(self) -> None:
        """启动 Uvicorn、Scheduler 和后台监控。"""

        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        scheduler = Scheduler(
            self.config,
            auto_shutdown=False,
            auto_schedule=False,
        )
        scheduler.bind_stop_event(self._stop_event)
        app = create_app(scheduler)
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=self.config.system.webhook_port,
                log_level=self.config.system.log_level.lower(),
                access_log=False,
                loop="none",
            )
        )

        server_task = asyncio.create_task(server.serve())
        while not server.started:
            if server_task.done():
                await server_task
                return
            await asyncio.sleep(0.05)
        self._ready.set()
        await self._stop_event.wait()

        server.should_exit = True
        if not server_task.done():
            try:
                await asyncio.wait_for(server_task, timeout=10)
            except asyncio.TimeoutError:
                server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)


def run_desktop_app(config: Config) -> None:
    """启动桌面软件，关闭窗口后退出后台服务。"""

    try:
        import webview
    except ImportError as exc:
        raise RuntimeError("缺少 pywebview 依赖，请先执行 uv sync") from exc

    backend = DesktopBackend(config)
    backend.start()
    try:
        webview.create_window(
            "AutoGame 任务管理",
            f"{backend.url}/ui/",
            width=1280,
            height=820,
            min_size=(960, 640),
            resizable=True,
        )
        webview.start(debug=False)
    finally:
        mlog.info("桌面窗口已关闭，正在停止后台服务")
        backend.stop()
