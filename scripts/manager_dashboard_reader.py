"""Read manager dashboard data in a process shared by HTTP requests."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path


def read_dashboard(root: str) -> dict:
    from manager_service import ManagerService
    from manager_store import ManagerStore

    return ManagerService(store=ManagerStore(root=root)).dashboard_snapshot()


class ManagerDashboardReader:
    """Keep dashboard calculations responsive while collection runs in the server."""

    def __init__(self, root: Path) -> None:
        self.root = str(root)
        self.pool = ProcessPoolExecutor(max_workers=1)

    def snapshot(self) -> dict:
        return self.pool.submit(read_dashboard, self.root).result(timeout=60)

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
