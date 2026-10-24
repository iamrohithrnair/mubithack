"""What Precedenz keeps about Mubit, and what it only mirrors from it.

Bookkeeping and display only: nothing that decides, acts, or records a decision
reads this module.

Task key. Mubit groups calls into a task by fingerprinting the model,
instructions, tools, tool choice and output format, and it trains on the
examples one task collects. An edit that changes any of these starts a new task
and strands the examples already kept. Precedenz computes its own key over the
same inputs when it builds an agent, keeps the last key seen per workstream in
memory, and flags a change so a person sees it before the examples are lost.
The key is local: it is not Mubit's fingerprint and is never sent.

Console mirror. Mubit owns training, testing and rollout. The map shows the
stage the Mubit console reports for a workstream's task, as the operator copied
it into a snapshot file, with its source and time beside it. Only Mubit's own
stage words are accepted and nothing is computed or guessed: without a snapshot,
or without a valid entry for the workstream, there is nothing to show.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import get_settings

# The stages and outcomes the Mubit console shows, verbatim.
STAGES = (
    "Collecting examples",
    "Training",
    "Passed",
    "Below target",
    "Prompt only",
    "Not tested",
    "Silent test",
    "Partial rollout",
    "Live",
)
CONSOLE_SOURCE = "Mubit console"
TASK_KEY_LENGTH = 16

_STAGE_BY_FOLDED = {stage.casefold(): stage for stage in STAGES}

_lock = threading.Lock()
_task_keys: dict[str, dict[str, Any]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def task_key(
    model_id: str,
    system_prompt: str,
    tool_specs: list[dict],
    output_schema: dict | None = None,
) -> str:
    """A short, stable key over what Mubit fingerprints a task by.

    The order the tools are listed in does not matter; every character of the
    model ID, the prompt, each tool spec and the output schema does.
    """
    payload = {
        "model": model_id,
        "instructions": system_prompt,
        "tools": sorted(_canonical(spec) for spec in tool_specs),
        "output": output_schema,
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:TASK_KEY_LENGTH]


def note_task_key(family: str, key: str) -> bool:
    """Remember the workstream's task key. True when it differs from the last one seen.

    The first key seen for a workstream is not a change. After a change the
    workstream stays flagged, with the time the new key arrived, for the life
    of the process: the examples collected under the old key stay stranded.
    """
    now = _now()
    with _lock:
        seen = _task_keys.get(family)
        if seen is None:
            _task_keys[family] = {"value": key, "changed": False, "since": now, "previous": None}
            return False
        if seen["value"] == key:
            return False
        _task_keys[family] = {"value": key, "changed": True, "since": now, "previous": seen["value"]}
        return True


def task_key_info(family: str) -> dict[str, Any] | None:
    """`{"value", "changed", "since", "previous"}` for the workstream, or None if never seen."""
    with _lock:
        seen = _task_keys.get(family)
        return dict(seen) if seen is not None else None


def forget_task_keys() -> None:
    """Drop every remembered key, as a process restart would."""
    with _lock:
        _task_keys.clear()


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def console_stage(family: str) -> dict[str, Any] | None:
    """The stage the Mubit console reports for the workstream, from the operator's snapshot.

    The snapshot is `{"workstreams": {family: {"stage", "detail", "as_of"}}}`.
    Returns `{"stage", "detail", "source", "as_of"}`, or None when the file is
    missing or unreadable, the workstream has no entry, or the stage is not one
    of Mubit's own words. A stage word is matched without regard to case and
    always shown in Mubit's spelling.
    """
    path = get_settings().mubit_console_snapshot
    if path is None:
        return None
    try:
        snapshot = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    workstreams = snapshot.get("workstreams") if isinstance(snapshot, dict) else None
    entry = workstreams.get(family) if isinstance(workstreams, dict) else None
    if not isinstance(entry, dict):
        return None
    stage = entry.get("stage")
    stage = _STAGE_BY_FOLDED.get(stage.strip().casefold()) if isinstance(stage, str) else None
    if stage is None:
        return None
    return {
        "stage": stage,
        "detail": _text(entry.get("detail")),
        "source": CONSOLE_SOURCE,
        "as_of": _text(entry.get("as_of")),
    }
