"""启动 pywebview，并在后台事件循环管理任务。"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from autogame.config import Config
from autogame.desktop.bridge import DesktopBridge
from autogame.logger import mlog
from autogame.task_manager import TaskManager


class DesktopBackend:
    """在独立线程中运行桌面模式的任务监控事件循环。"""

    def __init__(self, config: Config) -> None:
        self.manager = TaskManager(config)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    def start(self) -> None:
        """启动后台事件循环并等待初始化完成。"""

        self._thread = threading.Thread(
            target=self._thread_main,
            name="autogame-desktop",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=15):
            raise RuntimeError("桌面后台启动超时")
        if self._error is not None:
            raise RuntimeError(f"桌面后台启动失败：{self._error}") from self._error

    def call(
        self,
        function: Callable[..., Any],
        *args: object,
        timeout: float = 30,
    ) -> Any:
        """在后台事件循环线程执行一个同步方法。"""

        async def invoke() -> Any:
            return function(*args)

        return self.submit(invoke(), timeout=timeout)

    def submit(self, coroutine: Coroutine[Any, Any, Any], timeout: float = 300) -> Any:
        """在线程安全的前提下提交一个异步任务。"""

        if self._loop is None:
            raise RuntimeError("桌面后台尚未启动")
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        return future.result(timeout=timeout)

    def stop(self) -> None:
        """请求任务监控和后台线程退出。"""

        if self._loop is not None and self._stop_event is not None:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread is not None:
            self._thread.join(timeout=15)

    def _thread_main(self) -> None:
        """在线程中创建和运行异步事件循环。"""

        try:
            asyncio.run(self._run())
        except BaseException as exc:
            self._error = exc
            self._ready.set()

    async def _run(self) -> None:
        """运行任务监控，直到桌面窗口请求停止。"""

        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._ready.set()
        try:
            await self.manager.monitor_loop(self._stop_event)
        finally:
            await self.manager.shutdown()


def run_desktop_app(config: Config) -> None:
    """启动桌面窗口；关闭窗口后停止后台任务管理器。"""

    try:
        import webview
    except ImportError as exc:
        raise RuntimeError(
            "缺少桌面依赖，请执行 uv sync"
        ) from exc

    backend = DesktopBackend(config)
    bridge = DesktopBridge(backend)
    page = Path(__file__).resolve().parent / "ui" / "index.html"
    backend.start()
    try:
        window = webview.create_window(
            "AutoGame",
            page.as_uri(),
            js_api=bridge,
            width=1280,
            height=820,
            min_size=(960, 640),
            resizable=True,
            frameless=True,
            easy_drag=False,
        )
        bridge._bind_window(window)
        webview.start(debug=False)
    finally:
        mlog.info("桌面窗口已关闭，正在停止任务管理器")
        backend.stop()
