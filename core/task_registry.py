"""注册内置任务的业务执行方式和完成信号。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal


CompletionSignal = Literal["internal", "maa_post", "maa_get"]


@dataclass(frozen=True)
class TaskDefinition:
    """描述一个任务的固定业务行为。"""

    name: str
    completion_signal: CompletionSignal
    runner: Callable[[], object] | None = None


def _运行明日方舟签到() -> object:
    """调用内置签到任务，不从配置文件导入任意 Python 入口。"""

    from tasks.skyland_sign.skyland import start

    return start()


TASK_DEFINITIONS: dict[str, TaskDefinition] = {
    "skyland_sign": TaskDefinition(
        name="skyland_sign",
        completion_signal="internal",
        runner=_运行明日方舟签到,
    ),
    "maa": TaskDefinition(name="maa", completion_signal="maa_post"),
    "maaend": TaskDefinition(name="maaend", completion_signal="maa_get"),
}


def get_task_definition(task_name: str) -> TaskDefinition:
    """返回任务定义；未注册任务按无外部应用的空任务处理。"""

    return TASK_DEFINITIONS.get(
        task_name,
        TaskDefinition(name=task_name, completion_signal="internal"),
    )
