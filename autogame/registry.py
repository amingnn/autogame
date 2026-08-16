"""注册任务名称和对应的生命周期适配器。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from autogame.config import Config
from autogame.tasks.base import TaskAdapter
from autogame.tasks.maa import MaaAdapter
from autogame.tasks.maaend import MaaEndAdapter
from autogame.tasks.skyland_sign import SkylandSignAdapter


TaskFactory = Callable[[Config], TaskAdapter]


@dataclass(frozen=True)
class TaskDefinition:
    """描述一个任务的固定适配器和页面元信息。"""

    name: str
    task_factory: TaskFactory
    description: str
    requires_script: bool


TASK_DEFINITIONS: dict[str, TaskDefinition] = {
    "skyland_sign": TaskDefinition(
        name="skyland_sign",
        task_factory=lambda config: SkylandSignAdapter(
            config.paths.skyland_token_file,
            config.tasks.get("skyland_sign").account
            if config.tasks.get("skyland_sign")
            else None,
        ),
        description=SkylandSignAdapter.description,
        requires_script=False,
    ),
    "maa": TaskDefinition(
        name="maa",
        task_factory=lambda _config: MaaAdapter(),
        description=MaaAdapter.description,
        requires_script=True,
    ),
    "maaend": TaskDefinition(
        name="maaend",
        task_factory=lambda _config: MaaEndAdapter(),
        description=MaaEndAdapter.description,
        requires_script=True,
    ),
}


def get_task_definition(task_name: str) -> TaskDefinition | None:
    """返回已注册任务；未知任务不会被当作空任务执行。"""

    return TASK_DEFINITIONS.get(task_name)
