"""Live events for the map, in memory and in one process.

Like app/progress.py, this is a side channel: nothing that decides, acts, or
records ever reads it. Losing an event costs an animation and nothing else. The
SSE route reads it with `since(seq)`; every publisher calls `publish(type, ...)`.

Types (docs/decide-once-plan.md, 5.6): decision.started, model.call,
decision.finished, precedent.decided, precedent.overruled, workstream.planted,
case.answered, outcome.reported.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

MAX_EVENTS = 2000

_lock = threading.Condition()
_events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
_seq = 0


def publish(event_type: str, **fields: Any) -> dict[str, Any]:
    """Append one event and wake every waiting reader. Never raises."""
    global _seq
    with _lock:
        _seq += 1
        event = {
            "seq": _seq,
            "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "type": event_type,
            **fields,
        }
        _events.append(event)
        _lock.notify_all()
        return event


def since(seq: int) -> list[dict[str, Any]]:
    """Every retained event with a sequence number above `seq`, oldest first."""
    with _lock:
        return [e for e in _events if e["seq"] > seq]


def wait(seq: int, timeout: float = 15.0) -> list[dict[str, Any]]:
    """Block until an event newer than `seq` exists or the timeout passes."""
    with _lock:
        if _seq <= seq:
            _lock.wait(timeout)
        return [e for e in _events if e["seq"] > seq]


def last_seq() -> int:
    with _lock:
        return _seq


def clear() -> None:
    global _seq
    with _lock:
        _events.clear()
        _seq = 0
