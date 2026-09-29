"""Persistent polling intervals shared by manager services and the dashboard."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from service_errors import ServiceError

POLLING_FIELDS = {
    "health_accounts_seconds": (600, 30, 3600, "机器与账号检查"),
    "results_seconds": (300, 30, 3600, "执行结果回收"),
    "agent_seconds": (5, 1, 300, "经理 Agent 任务轮询"),
    "memory_review_seconds": (60, 10, 3600, "记忆经验审核"),
    "dashboard_seconds": (30, 5, 600, "运营台页面刷新"),
    "task_detail_seconds": (5, 1, 60, "任务详情刷新"),
}
DEFAULT_POLLING = {name: spec[0] for name, spec in POLLING_FIELDS.items()}


class ManagerPolling:
    """Keep interval reads local to the running manager; save updates atomically."""

    def _init_polling(self) -> None:
        self._polling_lock = threading.Lock()
        self._polling_record = (
            self.store.read_view(copy_on_read=False).get("settings", "polling") or {}
        )

    @property
    def polling_settings(self) -> dict:
        return {**DEFAULT_POLLING, **self._polling_record.get("intervals", {})}

    def get_polling_settings(self) -> dict:
        return {
            "success": True,
            "intervals": self.polling_settings,
            "updated_at": self._polling_record.get("updated_at"),
            "status_stale_seconds": self.polling_settings["health_accounts_seconds"] * 3,
            "fields": [
                {"key": key, "default": default, "min": minimum, "max": maximum, "label": label}
                for key, (default, minimum, maximum, label) in POLLING_FIELDS.items()
            ],
        }

    def update_polling_settings(self, values: dict) -> dict:
        for key, value in values.items():
            if key not in POLLING_FIELDS:
                raise ServiceError("INVALID_POLLING_SETTING", "未知轮询配置项", 400)
            _, minimum, maximum, label = POLLING_FIELDS[key]
            if type(value) is not int or not minimum <= value <= maximum:
                raise ServiceError(
                    "INVALID_POLLING_INTERVAL",
                    f"{label}必须为 {minimum} 到 {maximum} 之间的整数秒",
                    400,
                )
        with self._polling_lock:
            record = {
                "intervals": {**self.polling_settings, **values},
                "updated_at": datetime.now(UTC).isoformat(),
            }
            self.store.put("settings", "polling", record)
            self._polling_record = record
        return {**self.get_polling_settings(), "message": "轮询配置已保存并生效"}
