"""提供跨进程自动化实例锁和任务执行锁。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from filelock import FileLock, Timeout


@dataclass
class ExecutionLease:
    """表示当前进程持有的一把执行锁。"""

    name: str
    _lock: FileLock
    _released: bool = False

    def release(self) -> None:
        """释放执行锁；重复调用不会产生影响。"""

        if self._released:
            return
        self._lock.release()
        self._released = True


class ExecutionLock:
    """在数据目录中创建具名跨进程锁。"""

    def __init__(self, lock_dir: Path) -> None:
        self.lock_dir = lock_dir

    def acquire(self, name: str) -> ExecutionLease | None:
        """立即尝试获取锁；已被占用时返回 ``None``。"""

        self.lock_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
        lock = FileLock(str(self.lock_dir / f"{safe_name}.lock"))
        try:
            lock.acquire(timeout=0)
        except Timeout:
            return None
        return ExecutionLease(name=name, _lock=lock)
