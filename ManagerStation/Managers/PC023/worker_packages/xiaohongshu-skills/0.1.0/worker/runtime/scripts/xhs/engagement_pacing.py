"""Account-level timing for approved home-feed operations."""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

MIN_READ_SECONDS = 15.0
MAX_READ_SECONDS = 45.0
MIN_LIKE_INTERVAL_SECONDS = 90.0
MAX_LIKE_INTERVAL_SECONDS = 180.0
UNLIKED_NOTES_BETWEEN_LIKES = 2


class EngagementPacing:
    """Keep the next like time and intervening browse count across account tasks."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.state = (
            json.loads(path.read_text(encoding="utf-8"))
            if path is not None and path.exists()
            else {"next_like_at": 0.0, "unliked_notes": UNLIKED_NOTES_BETWEEN_LIKES}
        )

    @property
    def notes_ready(self) -> bool:
        return self.state["unliked_notes"] >= UNLIKED_NOTES_BETWEEN_LIKES

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.state["next_like_at"] - time.time())

    def record_attempt(self) -> float:
        interval = random.uniform(MIN_LIKE_INTERVAL_SECONDS, MAX_LIKE_INTERVAL_SECONDS)
        self.state.update(next_like_at=time.time() + interval, unliked_notes=0)
        self._save()
        return interval

    def record_unliked_note(self) -> None:
        self.state["unliked_notes"] += 1
        self._save()

    def _save(self) -> None:
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)
