"""Read manager dashboard data in a process shared by HTTP requests."""

from __future__ import annotations

from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path
from threading import Lock


def read_dashboard(root: str) -> dict:
    from manager_service import ManagerService
    from manager_store import ManagerStore

    return ManagerService(store=ManagerStore(root=root)).dashboard_snapshot()


class ManagerDashboardReader:
    """Keep dashboard calculations responsive while collection runs in the server."""

    def __init__(self, root: Path) -> None:
        self.root = str(root)
        self.pool = ProcessPoolExecutor(max_workers=1)
        self._lock = Lock()
        self._pending: Future[dict] | None = None

    def snapshot(self) -> dict:
        with self._lock:
            if self._pending is None or self._pending.done():
                self._pending = self.pool.submit(read_dashboard, self.root)
            pending = self._pending
        return pending.result(timeout=60)

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
