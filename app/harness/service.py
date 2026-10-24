"""The decision service: one case in, one receipt out, never an action.

    request -> engine for the workstream -> follow or ask -> receipt, audit, event

Every caller (the HTTP API, the CLI and MCP server through it, and the
re-assessment of waiting asks after a person remembers an answer) comes through
`decide`. It never raises for anything that goes wrong while deciding: a model,
tool, Sapling or engine failure is an ask that names the failure. It raises only
for what the caller got wrong: an unknown workstream, or an idempotency key
reused with a different input.

Receipts are immutable. The same (workstream, idempotency key) with the same
input returns the first receipt unchanged; with a different input it raises
`IdempotencyConflict`.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import threading
import uuid
from typing import Any

from app import audit, db
from app.agent.providers import CallLog
from app.audit import AuditEventType
from app.domain import PolicyStatus
from app.harness import bootstrap, events, store
from app.harness.engines import ASK, FOLLOW, Assessment, Check, DecideRequest, engine_for
from app.policy import store as policy_store

log = logging.getLogger(__name__)

# One decision at a time in this process. SQLite allows one writer, and a
# returns decision holds the write lock across its model call; serializing here
# makes concurrent callers queue instead of hitting "database is locked". Re-entrant,
# so a caller already holding it (re-assessing waiting asks) can still decide.
_DECISION_LOCK = threading.RLock()


def decision_lock() -> threading.RLock:
    """The lock every harness write takes. Hold it to write alongside `decide`."""
    return _DECISION_LOCK


class UnknownWorkstream(KeyError):
    """No engine is registered for this workstream. The API answers 404."""


class UnknownDecision(KeyError):
    """No decision has this id. The API answers 404."""


class IdempotencyConflict(Exception):
    """The idempotency key was already used with a different input. The API answers 409."""

    def __init__(self, workstream: str, idempotency_key: str, decision_id: str) -> None:
        super().__init__(
            f"idempotency key '{idempotency_key}' was already used in '{workstream}' with a "
            f"different input (decision {decision_id})"
        )
        self.decision_id = decision_id


def input_hash(request: DecideRequest) -> str:
    """What the caller asked, canonically. The caller label is not part of it."""
    material = {
        "workstream": request.workstream,
        "case_ref": request.case_ref,
        "facts": request.facts,
        "text": request.text,
        "source_revision": request.source_revision,
    }
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _trace_id() -> str:
    return f"trc_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------- decide


def decide(request: DecideRequest, *, resolves: str | None = None) -> dict[str, Any]:
    """Follow or ask for one case, and return its receipt."""
    try:
        bootstrap.load()
    except Exception:  # a stored workstream that will not load must not stop the rest
        # The built-in engines register first, so they still decide; the broken
        # workstream reads as unknown until it loads, and the next call retries.
        log.exception("could not load every workstream engine")
    try:
        engine = engine_for(request.workstream)
    except KeyError:
        raise UnknownWorkstream(f"unknown workstream '{request.workstream}'") from None

    digest = input_hash(request)
    with _DECISION_LOCK:
        with db.read_only() as conn:
            existing = store.find_by_key(conn, request.workstream, request.idempotency_key)
            if existing is not None:
                return _first_receipt(conn, existing, digest)

        trace_id = _trace_id()
        events.publish(
            "decision.started",
            workstream=request.workstream,
            case_ref=request.case_ref,
            caller=request.caller,
            via="api",
        )
        calls = CallLog(request.workstream, request.case_ref)
        assessment = _assess(engine, request, trace_id=trace_id, calls=calls)
        receipt, fresh = _record(request, assessment, calls, digest, trace_id, resolves)

    if fresh:
        events.publish(
            "decision.finished",
            decision_id=receipt["decision_id"],
            workstream=receipt["workstream"],
            case_ref=receipt["case_ref"],
            verdict=receipt["verdict"],
            precedent_id=(receipt["precedent"] or {}).get("id"),
            reasons=receipt["reasons"],
            resolves=receipt["resolves"],
            caller=receipt["caller"],
            via="api",
        )
    return receipt


def _first_receipt(conn: Any, row: Any, digest: str) -> dict[str, Any]:
    if row["input_hash"] != digest:
        raise IdempotencyConflict(row["workstream"], row["idempotency_key"], row["decision_id"])
    return store.receipt(conn, row)


def _assess(engine: Any, request: DecideRequest, *, trace_id: str, calls: CallLog) -> Assessment:
    """Run the engine in its own transaction. Whatever goes wrong becomes an ask."""
    try:
        with db.session() as conn:
            assessment = engine.assess(conn, request, trace_id=trace_id, calls=calls)
            if not isinstance(assessment, Assessment):
                raise TypeError(f"engine returned {type(assessment).__name__}, not an Assessment")
            return _hold_to_the_rules(conn, request, assessment)
    except Exception as exc:  # model, tool, Sapling, database: never an error, always an ask
        reason = f"{type(exc).__name__}: {exc}"
        return Assessment.ask(reason, checks=(Check("assessment completed", False, reason),))


def _hold_to_the_rules(conn: Any, request: DecideRequest, assessment: Assessment) -> Assessment:
    """The follow invariants, checked once more whatever the engine said.

    A follow needs an active precedent of this workstream, no reasons against
    it, and every check passed. Its actions are the precedent's own, never the
    engine's. An ask carries no actions.
    """
    if assessment.verdict != FOLLOW:
        return dataclasses.replace(assessment, verdict=ASK, actions=())

    def refuse(reason: str) -> Assessment:
        return dataclasses.replace(
            assessment,
            verdict=ASK,
            actions=(),
            reasons=(*assessment.reasons, reason),
            checks=(*assessment.checks, Check("follow invariants", False, reason)),
        )

    if not assessment.precedent_id:
        return refuse("a follow needs a precedent, and the engine named none")
    failed = [c.name for c in assessment.checks if not c.ok]
    if failed:
        return refuse(f"a follow needs every check passed; failed: {', '.join(failed)}")
    if assessment.reasons:
        return refuse("a follow cannot carry reasons against it")
    try:
        record = policy_store.get(conn, assessment.precedent_id)
    except KeyError:
        return refuse(f"precedent {assessment.precedent_id} does not exist")
    if record.family != request.workstream:
        return refuse(f"precedent {record.policy_id} belongs to '{record.family}'")
    if record.status != PolicyStatus.ACTIVE:
        return refuse(f"precedent {record.policy_id} is '{record.status}', not active")
    return dataclasses.replace(
        assessment,
        precedent_version=record.version,
        actions=tuple(a.model_dump() for a in record.definition.actions),
    )


def _record(
    request: DecideRequest,
    assessment: Assessment,
    calls: CallLog,
    digest: str,
    trace_id: str,
    resolves: str | None,
) -> tuple[dict[str, Any], bool]:
    """Write the receipt and its audit entry. (receipt, whether this call wrote it)."""
    created = store.now()
    decision_id = store.new_decision_id()
    row = {
        "decision_id": decision_id,
        "workstream": request.workstream,
        "case_ref": request.case_ref,
        "idempotency_key": request.idempotency_key,
        "input_hash": digest,
        "verdict": assessment.verdict,
        "precedent_id": assessment.precedent_id,
        "precedent_version": assessment.precedent_version,
        "actions": [dict(a) for a in assessment.actions],
        "reasons": list(assessment.reasons),
        "checks": [c.to_dict() for c in assessment.checks],
        "model_calls": [_call_dict(c) for c in calls],
        "task_key": getattr(calls, "task_key", None),
        "caller": request.caller,
        "trace_id": trace_id,
        "resolves": resolves,
        "source_revision": request.source_revision,
        "created_at": store.iso(created),
        "expires_at": store.iso(created + store.RECEIPT_TTL),
    }
    with db.session() as conn:
        if not store.insert_decision(conn, row):
            # Another writer took this key between our read and our write.
            existing = store.find_by_key(conn, request.workstream, request.idempotency_key)
            return _first_receipt(conn, existing, digest), False
        audit.record(
            conn,
            trace_id=trace_id,
            event_type=AuditEventType.HARNESS_DECIDED,
            actor="harness",
            case_id=request.case_ref,
            policy_id=assessment.precedent_id,
            payload={
                "decision_id": decision_id,
                "workstream": request.workstream,
                "verdict": assessment.verdict,
                "precedent_version": assessment.precedent_version,
                "reasons": row["reasons"],
                "checks": [f"{'ok' if c.ok else 'FAILED'}: {c.name}" for c in assessment.checks],
                "actions": row["actions"],
                "model_request_ids": [c.get("request_id") for c in row["model_calls"]],
                "task_key": row["task_key"],
                "input_hash": digest,
                "idempotency_key": request.idempotency_key,
                "caller": request.caller,
                "resolves": resolves,
            },
        )
        return store.receipt(conn, store.get_row(conn, decision_id)), True


def _call_dict(call: Any) -> dict[str, Any]:
    if hasattr(call, "to_dict"):
        return call.to_dict()
    return dict(call) if isinstance(call, dict) else {"via": "mubit", "detail": str(call)}


# ------------------------------------------------------------------ reads


def get_receipt(decision_id: str) -> dict[str, Any] | None:
    """The receipt, plus what was appended to it since: answers, outcomes, resolution."""
    with db.read_only() as conn:
        row = store.get_row(conn, decision_id)
        if row is None:
            return None
        receipt = store.receipt(conn, row)
        receipt["answers"] = store.answers_for(conn, decision_id)
        receipt["outcomes"] = store.outcomes_for(conn, decision_id)
        receipt["resolved_by"] = store.resolved_by(conn, decision_id)
        return receipt


def open_questions(workstream: str | None = None) -> list[dict[str, Any]]:
    """Asks still waiting for a person: unanswered, and not resolved by a later decision."""
    with db.read_only() as conn:
        return [store.receipt(conn, row) for row in store.open_ask_rows(conn, workstream)]


def list_decisions(
    *, workstream: str | None = None, verdict: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    """Recent receipts, newest first."""
    with db.read_only() as conn:
        rows = store.list_rows(conn, workstream=workstream, verdict=verdict, limit=limit)
        return [store.receipt(conn, row) for row in rows]


# ---------------------------------------------------------------- outcomes


def report_outcome(decision_id: str, status: str, detail: str, actor: str) -> dict[str, Any]:
    """The caller says what happened when it carried a decision out.

    Appended, never verified: Precedenz did not act, so it cannot read the
    result back. The outcome says so ("reported").
    """
    if status not in store.OUTCOME_STATUSES:
        raise ValueError(f"status must be one of {', '.join(store.OUTCOME_STATUSES)}")
    with _DECISION_LOCK:
        with db.session() as conn:
            row = store.get_row(conn, decision_id)
            if row is None:
                raise UnknownDecision(f"no decision '{decision_id}'")
            outcome = store.insert_outcome(
                conn, decision_id, status=status, detail=detail, actor=actor
            )
            audit.record(
                conn,
                trace_id=row["trace_id"],
                event_type=AuditEventType.HARNESS_OUTCOME_REPORTED,
                actor=actor,
                case_id=row["case_ref"],
                policy_id=row["precedent_id"],
                payload={
                    "decision_id": decision_id,
                    "outcome_id": outcome["outcome_id"],
                    "status": status,
                    "detail": detail,
                    "verification": outcome["verification"],
                },
            )
    events.publish("outcome.reported", decision_id=decision_id, status=status)
    return outcome
