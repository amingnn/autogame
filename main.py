"""AutoGame 启动入口。"""

from __future__ import annotations

import argparse
from pathlib import Path

from autogame.config import Config
from autogame.logger import configure_logging, mlog


def _parse_args() -> argparse.Namespace:
    """解析唯一的自动化启动参数。"""

    parser = argparse.ArgumentParser(description="AutoGame 自动化任务程序")
    parser.add_argument(
        "--automation",
        action="store_true",
        help="以无界面自动化模式运行",
    )
    return parser.parse_args()


def main() -> None:
    """根据启动参数进入桌面模式或自动化模式。"""

    arguments = _parse_args()
    config_path = Path(__file__).resolve().parent / "config.yaml"
    config = Config.load(config_path)
    configure_logging(config.log_dir, config.system.log_level, force=True)
    mlog.info("配置加载完成")

    if arguments.automation:
        import asyncio

        from autogame.automation import run_automation

        asyncio.run(run_automation(config))
    else:
        from autogame.desktop import run_desktop_app

        run_desktop_app(config)


if __name__ == "__main__":
    main()
