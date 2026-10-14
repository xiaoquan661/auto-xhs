"""Task-scoped progress emitted by browser adapters while they execute."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_recorder: ContextVar[Callable[[dict], None] | None] = ContextVar("task_progress", default=None)


@contextmanager
def record_progress_with(recorder: Callable[[dict], None]) -> Iterator[None]:
    token = _recorder.set(recorder)
    try:
        yield
    finally:
        _recorder.reset(token)


def emit_progress(
    stage: str, summary: str, *, metrics: dict | None = None, **details: object
) -> None:
    recorder = _recorder.get()
    if recorder is not None:
        try:
            recorder({"stage": stage, "summary": summary, "metrics": metrics or {}, **details})
        except Exception:
            logging.getLogger(__name__).exception("任务进度记录失败")
