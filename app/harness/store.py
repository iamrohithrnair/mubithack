"""Decision receipts, and the answers and outcomes appended to them.

A receipt is written once and never edited. Everything that happens to a
decision afterwards (a person's answer, the caller's reported outcome, a later
decision that resolves an ask) is a row of its own, joined on read. Tables:
app/db/migrations/0004_harness.*.sql.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.db import Connection

from app.policy import store as policy_store

SERVING_IDENTITY = "not reported by the gateway"
# How long a follow stays good for: the caller should act on it now or ask again,
# because the source systems (and the precedent) may have moved on.
RECEIPT_TTL = timedelta(minutes=15)
OUTCOME_STATUSES = ("executed", "failed")


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds")


def new_decision_id() -> str:
    return f"dec_{uuid.uuid4().hex[:16]}"


# ----------------------------------------------------------------- precedents


def precedent_text(definition: Any) -> str:
    """The precedent in words: what the person wrote, or else its conditions."""
    text = (getattr(definition, "description", "") or "").strip()
    return text or "; ".join(definition.condition_summary())


def precedent_view(record: policy_store.PolicyRecord) -> dict[str, Any]:
    """A precedent as a receipt shows it. Only fields that never change after activation."""
    return {
        "id": record.policy_id,
        "version": record.version,
        "text": precedent_text(record.definition),
        "decided_by": record.activated_by,
        "decided_at": record.activated_at,
        "origin_case": record.origin_case_id,
    }


def _precedent_for(conn: "Connection", policy_id: str | None, version: int | None) -> dict | None:
    if not policy_id:
        return None
    try:
        return precedent_view(policy_store.get(conn, policy_id))
    except KeyError:
        return {
            "id": policy_id,
            "version": version,
            "text": None,
            "decided_by": None,
            "decided_at": None,
            "origin_case": None,
        }


# ------------------------------------------------------------------ decisions


_COLUMNS = (
    "decision_id",
    "workstream",
    "case_ref",
    "idempotency_key",
    "input_hash",
    "verdict",
    "precedent_id",
    "precedent_version",
    "actions",
    "reasons",
    "checks",
    "model_calls",
    "task_key",
    "caller",
    "trace_id",
    "resolves",
    "source_revision",
    "created_at",
    "expires_at",
    # The facts this decision was assessed on (migration 0005). The case row is
    # latest-wins; this is what a person answering this decision actually saw.
    "facts",
)
_JSON_COLUMNS = ("actions", "reasons", "checks", "model_calls", "facts")
_JSON_DEFAULTS: dict[str, Any] = {"facts": {}}


def insert_decision(conn: "Connection", row: dict[str, Any]) -> bool:
    """Write one receipt. False when the idempotency key is already taken.

    Conditional rather than select-then-insert, like the inbox's exceptions:
    two writers racing on one key are decided by the unique index, and the
    loser re-reads the winner's receipt instead of failing.
    """
    values = [
        json.dumps(row.get(c, _JSON_DEFAULTS.get(c)) if c in _JSON_DEFAULTS else row[c], default=str)
        if c in _JSON_COLUMNS
        else row.get(c)
        for c in _COLUMNS
    ]
    inserted = conn.execute(
        f"""INSERT INTO harness_decisions ({", ".join(_COLUMNS)})
            VALUES ({", ".join("?" for _ in _COLUMNS)})
            ON CONFLICT (workstream, idempotency_key) DO NOTHING
            RETURNING decision_id""",
        tuple(values),
    ).fetchone()
    return inserted is not None


def find_by_key(conn: "Connection", workstream: str, idempotency_key: str):
    return conn.execute(
        "SELECT * FROM harness_decisions WHERE workstream = ? AND idempotency_key = ?",
        (workstream, idempotency_key),
    ).fetchone()


def get_row(conn: "Connection", decision_id: str):
    return conn.execute(
        "SELECT * FROM harness_decisions WHERE decision_id = ?", (decision_id,)
    ).fetchone()


def receipt(conn: "Connection", row: Any) -> dict[str, Any]:
    """The receipt for one stored decision, exactly as `decide` first returned it."""
    return {
        "decision_id": row["decision_id"],
        "verdict": row["verdict"],
        "workstream": row["workstream"],
        "case_ref": row["case_ref"],
        "precedent": _precedent_for(conn, row["precedent_id"], row["precedent_version"]),
        "actions": json.loads(row["actions"] or "[]"),
        "reasons": json.loads(row["reasons"] or "[]"),
        "checks": json.loads(row["checks"] or "[]"),
        "model_calls": json.loads(row["model_calls"] or "[]"),
        "serving_identity": SERVING_IDENTITY,
        "task_key": row["task_key"],
        "input_hash": row["input_hash"],
        "source_revision": row["source_revision"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "resolves": row["resolves"],
        "caller": row["caller"],
        "idempotency_key": row["idempotency_key"],
        "trace_id": row["trace_id"],
    }


def list_rows(
    conn: "Connection",
    *,
    workstream: str | None = None,
    verdict: str | None = None,
    limit: int = 100,
) -> list[Any]:
    """Newest first."""
    clauses, params = [], []
    if workstream:
        clauses.append("workstream = ?")
        params.append(workstream)
    if verdict:
        clauses.append("verdict = ?")
        params.append(verdict)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return conn.execute(
        f"SELECT * FROM harness_decisions {where} ORDER BY created_at DESC, decision_id LIMIT ?",
        (*params, int(limit)),
    ).fetchall()


def open_ask_rows(conn: "Connection", workstream: str | None = None) -> list[Any]:
    """Asks nobody has answered and no later decision has resolved. Oldest first."""
    params: list[Any] = ["ask"]
    scope = ""
    if workstream:
        scope = "AND d.workstream = ?"
        params.append(workstream)
    return conn.execute(
        f"""SELECT d.* FROM harness_decisions d
             WHERE d.verdict = ?
               {scope}
               AND NOT EXISTS (SELECT 1 FROM harness_answers a WHERE a.decision_id = d.decision_id)
               AND NOT EXISTS (SELECT 1 FROM harness_decisions r WHERE r.resolves = d.decision_id)
             ORDER BY d.created_at, d.decision_id""",
        tuple(params),
    ).fetchall()


def resolved_by(conn: "Connection", decision_id: str) -> str | None:
    row = conn.execute(
        """SELECT decision_id FROM harness_decisions WHERE resolves = ?
            ORDER BY created_at, decision_id LIMIT 1""",
        (decision_id,),
    ).fetchone()
    return row["decision_id"] if row else None


# -------------------------------------------------------- answers, outcomes


def answers_for(conn: "Connection", decision_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM harness_answers WHERE decision_id = ? ORDER BY created_at, answer_id",
        (decision_id,),
    ).fetchall()
    return [
        {
            "answer_id": r["answer_id"],
            "answer": r["answer"],
            "remembered": bool(r["remembered"]),
            "precedent_id": r["precedent_id"],
            "actor": r["actor"],
            "note": r["note"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def outcomes_for(conn: "Connection", decision_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM harness_outcomes WHERE decision_id = ? ORDER BY created_at, outcome_id",
        (decision_id,),
    ).fetchall()
    return [_outcome(r) for r in rows]


def insert_outcome(
    conn: "Connection", decision_id: str, *, status: str, detail: str, actor: str
) -> dict[str, Any]:
    """Append what the caller says happened. Recorded as reported, never as verified."""
    outcome_id = f"out_{uuid.uuid4().hex[:16]}"
    conn.execute(
        """INSERT INTO harness_outcomes (outcome_id, decision_id, status, detail, actor,
                                         verification, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (outcome_id, decision_id, status, detail, actor, "reported", iso(now())),
    )
    row = conn.execute(
        "SELECT * FROM harness_outcomes WHERE outcome_id = ?", (outcome_id,)
    ).fetchone()
    return _outcome(row)


def _outcome(row: Any) -> dict[str, Any]:
    return {
        "outcome_id": row["outcome_id"],
        "decision_id": row["decision_id"],
        "status": row["status"],
        "detail": row["detail"],
        "actor": row["actor"],
        "verification": row["verification"],
        "created_at": row["created_at"],
    }
