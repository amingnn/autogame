import asyncio

import requests

from core.common import cfg
from tasks.skyland_sign.skyland import start


async def run() -> None:
    try:
        await asyncio.to_thread(start)
    finally:
        port = cfg.system.webhook_port
        try:
            requests.post(
                f"http://localhost:{port}/done",
                json={"task": "skyland_sign"},
                timeout=5,
            )
        except Exception:
            pass
