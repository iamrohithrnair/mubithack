"""The facts each planted case was decided on, kept in `harness_cases`.

A planted workstream has no source system to re-derive facts from: the caller's
typed facts, or the reader's reading of the text, are the record. They are
stored once, already reconciled, so a waiting ask can be assessed again after a
precedent is born without another model call (docs/decide-once-plan.md, 5.3).

When the facts could not be confirmed (the reader failed, or it disagreed with
the caller), the case is stored as `unconfirmed`. Such a case can never follow
on stored facts; it has to be sent again.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.db import Connection
    from app.workstreams.contract import FieldSpec, WorkstreamContract

SOURCE_CALLER = "caller"
SOURCE_READER = "reader"
SOURCE_BOTH = "caller+reader"
SOURCE_HISTORY = "history"
SOURCE_UNCONFIRMED = "unconfirmed"

_TRUE = {"true", "yes", "y", "1"}
_FALSE = {"false", "no", "n", "0"}


@dataclass(frozen=True)
class PlantedFacts:
    """One planted case's facts, in the shape the policy engine and replay read."""

    case_id: str
    values: dict[str, Any] = field(default_factory=dict)
    text: str | None = None
    source: str = SOURCE_CALLER

    def policy_view(self) -> dict[str, object]:
        """The contract's fields and nothing else; an unknown field is None."""
        return dict(self.values)

    @property
    def confirmed(self) -> bool:
        return self.source != SOURCE_UNCONFIRMED


@dataclass(frozen=True)
class StoredCase:
    workstream: str
    case_ref: str
    facts: dict[str, Any]
    text: str | None
    source: str
    revision: str | None
    received_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ------------------------------------------------------------- normalizing


def coerce(spec: "FieldSpec", value: Any) -> Any:
    """One field's value in its canonical type. Raises ValueError naming the field."""
    name = spec.name
    if spec.kind == "flag":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in _TRUE | _FALSE:
            return value.strip().lower() in _TRUE
        raise ValueError(f"{name} must be true or false, got {value!r}")

    if spec.kind in ("amount", "count"):
        number: float
        if isinstance(value, bool):
            raise ValueError(f"{name} must be a number, got {value!r}")
        if isinstance(value, (int, float)):
            number = float(value)
        elif isinstance(value, str):
            try:
                number = float(value.strip().replace(",", "").lstrip("$"))
            except ValueError:
                raise ValueError(f"{name} must be a number, got {value!r}") from None
        else:
            raise ValueError(f"{name} must be a number, got {value!r}")
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"{name} must be zero or more, got {value!r}")
        if spec.kind == "count":
            if number != int(number):
                raise ValueError(f"{name} must be a whole number, got {value!r}")
            return int(number)
        return round(number, 2)

    # category
    allowed = spec.values or []
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned in allowed:
            return cleaned
        folded = {v.lower(): v for v in allowed}
        if cleaned.lower() in folded:
            return folded[cleaned.lower()]
    raise ValueError(f"{name} must be one of {', '.join(allowed)}, got {value!r}")


def normalize(
    contract: "WorkstreamContract", raw: dict[str, Any] | None
) -> tuple[dict[str, Any], list[str], list[str]]:
    """(values, problems, ignored keys). A None or absent value is simply unknown."""
    raw = raw or {}
    values: dict[str, Any] = {}
    problems: list[str] = []
    for spec in contract.fields:
        if raw.get(spec.name) is None:
            continue
        try:
            values[spec.name] = coerce(spec, raw[spec.name])
        except ValueError as exc:
            problems.append(str(exc))
    ignored = sorted(k for k in raw if contract.field_named(k) is None)
    return values, problems, ignored


def full_view(contract: "WorkstreamContract", values: dict[str, Any]) -> dict[str, Any]:
    """Every contract field, in contract order, with None for the unknown ones."""
    return {spec.name: values.get(spec.name) for spec in contract.fields}


def same(spec: "FieldSpec", a: Any, b: Any) -> bool:
    if spec.kind == "amount":
        return abs(float(a) - float(b)) < 0.005
    return a == b


# ---------------------------------------------------------------- storage


def load(conn: "Connection", workstream: str, case_ref: str) -> StoredCase | None:
    row = conn.execute(
        "SELECT * FROM harness_cases WHERE workstream = ? AND case_ref = ?",
        (workstream, case_ref),
    ).fetchone()
    if row is None:
        return None
    return StoredCase(
        workstream=row["workstream"],
        case_ref=row["case_ref"],
        facts=json.loads(row["facts"] or "{}"),
        text=row["text"],
        source=row["source"],
        revision=row["revision"],
        received_at=row["received_at"],
    )


def save(
    conn: "Connection",
    workstream: str,
    case_ref: str,
    *,
    facts: dict[str, Any],
    text: str | None,
    source: str,
    revision: str | None = None,
) -> None:
    """Record the facts this case is decided on. The latest arrival wins."""
    payload = json.dumps(facts, sort_keys=True)
    exists = conn.execute(
        "SELECT case_ref FROM harness_cases WHERE workstream = ? AND case_ref = ?",
        (workstream, case_ref),
    ).fetchone()
    if exists:
        conn.execute(
            """UPDATE harness_cases SET facts = ?, text = ?, source = ?, revision = ?, received_at = ?
                WHERE workstream = ? AND case_ref = ?""",
            (payload, text, source, revision, _now(), workstream, case_ref),
        )
    else:
        conn.execute(
            """INSERT INTO harness_cases (workstream, case_ref, facts, text, source, revision, received_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (workstream, case_ref, payload, text, source, revision, _now()),
        )


def facts_for(conn: "Connection", contract: "WorkstreamContract", case_ref: str) -> PlantedFacts:
    """The stored facts as the policy engine reads them. Raises LookupError if absent."""
    stored = load(conn, contract.id, case_ref)
    if stored is None:
        raise LookupError(f"no stored facts for {contract.subject} {case_ref}")
    values, _, _ = normalize(contract, stored.facts)
    return PlantedFacts(
        case_id=case_ref,
        values=full_view(contract, values),
        text=stored.text,
        source=stored.source,
    )
