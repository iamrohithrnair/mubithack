"""Workstream services: plant, answer and remember, overrule, and what exists.

These are the person-side operations of the decide-once loop:

    ask ─► a person answers ─► "remember this" ─► draft (5.3) ─► replay against
    past answers ─► activate through the gate ─► waiting asks it covers resolve

and its undo, overrule, which stops a precedent at once. Each call manages its
own transaction. The HTTP routes (app/api/workstreams.py) and the map's write
routes call these; the MCP server never can, because each needs a person.

Errors carry an HTTP status: NotFound 404, Invalid 422, Conflict 409,
Forbidden 403.
"""

from __future__ import annotations

import importlib
import json
import logging
import uuid
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from typing import Any, Iterator

from app import audit, config, db, domains
from app.audit import AuditEventType
from app.harness import events
from app.harness.engines import DecideRequest
from app.policy import store as policy_store
from app.policy.engine import match_policy
from app.policy.replay import replay_candidate_policy
from app.policy.schema import PolicyValidationError
from app.workstreams import cases, domain, drafting, registry
from app.workstreams.contract import BUILT_IN_IDS, WorkstreamContract

log = logging.getLogger(__name__)

TEMPLATES_DIR = config.REPO_ROOT / "fixtures" / "workstreams"
MAX_RESOLVE = 20
HISTORY_ACTOR = "template history (synthetic)"


class WorkstreamError(Exception):
    status_code = 400

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.errors = errors or [message]

    def detail(self) -> dict[str, Any]:
        return {"error": self.message, "errors": self.errors}


# Each also subclasses the builtin a generic caller would map: the map's write
# routes turn LookupError into 404, PermissionError into 403, and the rest into 409.
class NotFound(WorkstreamError, LookupError):
    status_code = 404


class Invalid(WorkstreamError, ValueError):
    status_code = 422


class Conflict(WorkstreamError, ValueError):
    status_code = 409


class Forbidden(WorkstreamError, PermissionError):
    status_code = 403


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@contextmanager
def _write_session() -> Iterator["db.Connection"]:
    """One write transaction, taken in turn with decisions.

    SQLite allows one writer, and a decision holds it across its model call.
    Taking the decision service's lock first means waiting for that decision
    instead of timing out on the database. Never held while deciding again.
    """
    try:
        from app.harness.service import decision_lock

        lock: Any = decision_lock()
    except ImportError:
        lock = nullcontext()
    with lock:
        with db.session() as conn:
            yield conn


def _trace_id() -> str:
    return f"trc_{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------- templates


def _templates() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(TEMPLATES_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        out[data["contract"]["id"]] = data
    return out


def load_template(template_id: str) -> dict[str, Any]:
    """A template file: its contract, 12 past answers, and 20 incoming examples."""
    found = _templates().get(template_id)
    if found is None:
        raise NotFound(f"no template '{template_id}'; templates: {', '.join(sorted(_templates()))}")
    return found


def list_templates() -> list[dict[str, Any]]:
    return [
        {
            "id": c["id"],
            "label": c["label"],
            "audience": c["audience"],
            "question": c["question"],
            "subject": c["subject"],
        }
        for c in (t["contract"] for t in _templates().values())
    ]


# -------------------------------------------------------------- workstreams


def _humanize(name: str) -> str:
    return name.replace("_", " ").capitalize()


def _builtin_entry(spec: domains.DomainSpec) -> dict[str, Any]:
    """A built-in workstream, described from its DomainSpec (bool is a flag,
    enum a category, number an amount)."""
    fields = [
        {"name": n, "kind": "flag", "label": _humanize(n), "ceiling": None}
        for n in sorted(spec.bool_fields)
    ]
    fields += [
        {"name": n, "kind": "category", "label": _humanize(n), "ceiling": None,
         "values": sorted(spec.enum_fields[n])}
        for n in sorted(spec.enum_fields)
    ]
    fields += [
        {"name": n, "kind": "amount", "label": _humanize(n), "ceiling": spec.numeric_ceilings.get(n)}
        for n in sorted(spec.number_fields)
    ]
    return {
        "id": spec.family,
        "kind": "built-in",
        "label": spec.label,
        "audience": spec.audience,
        "question": spec.question,
        "subject": spec.subject,
        "repeatable_answer": spec.repeatable_answer,
        "other_answers": list(spec.other_answers),
        "fields": fields,
        "always_ask": [],
        "reader": spec.reader,
        "evidence": "text" if spec.evidence_text is not None else "none",
    }


def _planted_entry(contract: WorkstreamContract, row: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": contract.id,
        "kind": "planted",
        "label": contract.label,
        "audience": contract.audience,
        "question": contract.question,
        "subject": contract.subject,
        "owner": contract.owner,
        "repeatable_answer": contract.repeatable_answer,
        "other_answers": list(contract.other_answers),
        "fields": [
            {"name": f.name, "kind": f.kind, "label": f.label, "ceiling": f.ceiling,
             **({"values": list(f.values)} if f.values else {}), "required": f.required}
            for f in contract.fields
        ],
        "always_ask": [domain.describe(contract, rule) for rule in contract.always_ask],
        "reader": contract.reader,
        "evidence": contract.evidence,
        "version": contract.version,
        "planted_by": row["planted_by"] if row else None,
        "planted_at": row["planted_at"] if row else None,
    }


def _stored_contract(conn: "db.Connection", workstream: str) -> tuple[WorkstreamContract, dict] | None:
    row = conn.execute("SELECT * FROM workstreams WHERE id = ?", (workstream,)).fetchone()
    if row is None:
        return None
    try:
        contract = registry.contract_from_row(row)
    except registry.ContractError as exc:
        raise Conflict(f"workstream '{workstream}' has a contract that no longer validates", exc.errors) from exc
    if workstream not in domains.families():
        registry.register(contract)
    return contract, row


def list_workstreams() -> list[dict[str, Any]]:
    """The built-in workstreams, then every planted one, oldest first."""
    domains.load()
    out = [_builtin_entry(domains.get(f)) for f in sorted(BUILT_IN_IDS) if f in domains.families()]
    with db.read_only() as conn:
        rows = conn.execute("SELECT * FROM workstreams ORDER BY planted_at, id").fetchall()
    for row in rows:
        try:
            out.append(_planted_entry(registry.contract_from_row(row), row))
        except registry.ContractError as exc:
            log.warning("workstream %s not listed: %s", row["id"], "; ".join(exc.errors))
    return out


def plant(
    template_id: str | None = None,
    contract: dict[str, Any] | None = None,
    *,
    actor: str,
) -> dict[str, Any]:
    """Plant a workstream from a template or a contract. No code.

    A template's past answers come with it: each becomes a stored case and an
    answer, so the first precedent is replayed against real history rather
    than nothing. Planting the same contract again changes nothing; planting a
    different contract under a planted id is a conflict.
    """
    if (template_id is None) == (contract is None):
        raise Invalid("plant from a template or from a contract: give exactly one")
    history: list[dict[str, Any]] = []
    if template_id is not None:
        template = load_template(template_id)
        payload, history = template["contract"], list(template.get("history") or [])
    else:
        payload = contract  # type: ignore[assignment]
    try:
        parsed = registry.parse_contract(payload)
    except registry.ContractError as exc:
        raise Invalid(str(exc), exc.errors) from exc

    past = _prepare_history(parsed, history)
    stored_json = parsed.model_dump_json()
    actor = (actor or "").strip() or "unknown"
    planted_now = False
    with _write_session() as conn:
        row = conn.execute("SELECT contract FROM workstreams WHERE id = ?", (parsed.id,)).fetchone()
        if row is not None:
            if json.loads(row["contract"]) != json.loads(stored_json):
                raise Conflict(
                    f"'{parsed.id}' is already planted with a different contract; "
                    "plant it under a new id"
                )
        else:
            planted_now = True
            conn.execute(
                """INSERT INTO workstreams (id, contract, version, audience, planted_by, planted_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (parsed.id, stored_json, parsed.version, parsed.audience, actor, _now()),
            )
            for case_ref, values, text, answer, note in past:
                cases.save(conn, parsed.id, case_ref, facts=values, text=text, source=cases.SOURCE_HISTORY)
                conn.execute(
                    """INSERT INTO harness_answers (answer_id, decision_id, answer, remembered,
                           precedent_id, actor, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        f"ans_{uuid.uuid4().hex[:12]}",
                        domain.history_decision_id(parsed.id, case_ref),
                        answer,
                        False,
                        None,
                        HISTORY_ACTOR,
                        note,
                        _now(),
                    ),
                )
        stored = conn.execute("SELECT * FROM workstreams WHERE id = ?", (parsed.id,)).fetchone()

    registry.register(parsed)
    if planted_now:
        events.publish(
            "workstream.planted", workstream=parsed.id, label=parsed.label, audience=parsed.audience
        )
    return {
        **_planted_entry(parsed, stored),
        "history_imported": len(past) if planted_now else 0,
        "already_planted": not planted_now,
    }


def _prepare_history(
    contract: WorkstreamContract, history: list[dict[str, Any]]
) -> list[tuple[str, dict[str, Any], str | None, str, str]]:
    out = []
    seen: set[str] = set()
    for i, item in enumerate(history):
        case_ref = str(item.get("case_ref") or "").strip()
        values, problems, _ = cases.normalize(contract, item.get("facts"))
        answer = item.get("answer")
        if not case_ref or case_ref in seen:
            problems.append("a unique case_ref is required")
        if answer not in contract.answers:
            problems.append(f"answer {answer!r} is not one of {contract.answers}")
        if problems:
            raise Invalid(f"history[{i}] is not valid", [f"history[{i}]: {p}" for p in problems])
        seen.add(case_ref)
        note = str(item.get("note") or "synthetic past answer from the template")[:500]
        out.append((case_ref, values, item.get("text"), str(answer), note))
    return out


# ------------------------------------------------------------------ answer


def precedent_summary(record: policy_store.PolicyRecord) -> dict[str, Any]:
    """The precedent as receipts and events show it (app.harness.store.precedent_view)."""
    from app.harness.store import precedent_view

    return {**precedent_view(record), "status": record.status}


def answer(
    decision_id: str,
    answer: str,
    *,
    remember: bool = False,
    up_to: dict[str, float] | float | None = None,
    ignore: list[str] | None = None,
    note: str = "",
    actor: str,
    approval_token: str,
) -> dict[str, Any]:
    """A person answers an ask, and may ask Precedenz to remember it.

    The answer is recorded either way. With `remember`, the case becomes a
    precedent: drafted per 5.3, replayed against every past answer in the
    workstream (zero false automatic actions, no coverage floor), and activated
    through the gate with the approval token. Then the waiting asks it covers
    are assessed again and resolve as follows. If the precedent cannot be
    activated, nothing is recorded and the reasons come back as a conflict.

    `up_to` is {field: value}; a bare number applies to the workstream's one
    amount field.
    """
    cfg = config.get_settings()
    if not approval_token or approval_token != cfg.approval_token:
        raise Forbidden("this needs a person: missing or invalid approval token")
    answer = (answer or "").strip()
    actor = (actor or "").strip() or "unknown"
    note = (note or "").strip()[:500]
    ignore = list(ignore or [])
    if not remember and (up_to or ignore):
        raise Invalid("up_to and ignore shape a precedent; they need remember")

    precedent: policy_store.PolicyRecord | None = None
    replay: dict[str, Any] | None = None
    contract: WorkstreamContract | None = None
    with _write_session() as conn:
        decision = conn.execute(
            "SELECT * FROM harness_decisions WHERE decision_id = ?", (decision_id,)
        ).fetchone()
        if decision is None:
            raise NotFound(f"decision '{decision_id}' not found")
        if decision["verdict"] != "ask":
            raise Conflict(
                f"decision '{decision_id}' followed a precedent; overrule the precedent instead"
            )
        family, case_ref = decision["workstream"], decision["case_ref"]
        found = _stored_contract(conn, family)
        if found is not None:
            contract = found[0]
            if answer not in contract.answers:
                raise Invalid(
                    f"'{answer}' is not an answer to {contract.label}; "
                    f"answers: {', '.join(contract.answers)}"
                )
            if remember and answer != contract.repeatable_answer:
                raise Invalid(
                    f"only '{contract.repeatable_answer}' can be remembered: a precedent repeats "
                    "that answer and no other"
                )
            widen = _up_to(contract, up_to)
        else:
            domains.load()
            if family not in BUILT_IN_IDS or family not in domains.families():
                raise NotFound(f"unknown workstream '{family}'")
            spec = domains.get(family)
            allowed = [a for a in (spec.repeatable_answer, *spec.other_answers) if a]
            if allowed and answer not in allowed:
                raise Invalid(f"'{answer}' is not an answer to {spec.label}; answers: {', '.join(allowed)}")
            if remember:
                raise Conflict(
                    "teach built-in workstreams in the inbox: remember applies to planted workstreams"
                )
        if not answer:
            raise Invalid("an answer is required")

        trace_id = decision["trace_id"] or _trace_id()
        answer_id = f"ans_{uuid.uuid4().hex[:12]}"
        conn.execute(
            """INSERT INTO harness_answers (answer_id, decision_id, answer, remembered,
                   precedent_id, actor, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (answer_id, decision_id, answer, False, None, actor, note, _now()),
        )
        if remember and contract is not None:
            precedent, replay = _remember(
                conn, contract, decision, up_to=widen, ignore=ignore, actor=actor,
                approval_token=approval_token, trace_id=trace_id,
            )
            conn.execute(
                "UPDATE harness_answers SET remembered = ?, precedent_id = ? WHERE answer_id = ?",
                (True, precedent.policy_id, answer_id),
            )
        audit.record(
            conn,
            trace_id=trace_id,
            event_type=AuditEventType.DECISION_RECORDED,
            actor=actor,
            case_id=case_ref,
            policy_id=precedent.policy_id if precedent else None,
            payload={
                "decision_id": decision_id,
                "workstream": family,
                "answer": answer,
                "remembered": precedent is not None,
                "note": note,
            },
        )

    events.publish("case.answered", decision_id=decision_id, answer=answer, remembered=precedent is not None)
    resolved: list[str] = []
    summary = None
    if precedent is not None and contract is not None:
        summary = precedent_summary(precedent)
        events.publish(
            "precedent.decided",
            precedent_id=precedent.policy_id,
            workstream=precedent.family,
            version=precedent.version,
            decided_by=summary["decided_by"],
            origin_case=summary["origin_case"],
            text=summary["text"],
        )
        resolved = resolve_waiting(contract, precedent, skip_case=case_ref)
    return {
        "answer_id": answer_id,
        "decision_id": decision_id,
        "workstream": family,
        "answer": answer,
        "remembered": precedent is not None,
        "precedent": (
            {"id": summary["id"], "version": summary["version"], "text": summary["text"]}
            if summary else None
        ),
        "replay": replay,
        "resolved": resolved,
    }


def _up_to(contract: WorkstreamContract, up_to: Any) -> dict[str, float]:
    """{field: number}. A bare number widens the workstream's one amount field."""
    if up_to is None or up_to == {}:
        return {}
    if not isinstance(up_to, dict):
        amounts = [f.name for f in contract.fields if f.kind == "amount"]
        if len(amounts) != 1:
            raise Invalid(f"{contract.label} has {len(amounts)} amount fields; name the one up_to widens")
        up_to = {amounts[0]: up_to}
    out: dict[str, float] = {}
    for name, value in up_to.items():
        try:
            if isinstance(value, bool):
                raise ValueError
            out[str(name)] = float(value)
        except (TypeError, ValueError):
            raise Invalid(f"up_to for '{name}' must be a number, got {value!r}") from None
    return out


def _remember(
    conn: "db.Connection",
    contract: WorkstreamContract,
    decision: dict[str, Any],
    *,
    up_to: dict[str, float],
    ignore: list[str],
    actor: str,
    approval_token: str,
    trace_id: str,
) -> tuple[policy_store.PolicyRecord, dict[str, Any]]:
    case_ref = decision["case_ref"]
    try:
        facts = cases.facts_for(conn, contract, case_ref)
    except LookupError as exc:
        raise Conflict(f"{exc}; it cannot become a precedent") from exc
    _, hard = domain.guard_checks(contract, facts)
    if hard:
        raise Conflict("this case cannot become a precedent: " + "; ".join(hard), hard)
    try:
        payload, _ = drafting.draft(contract, facts.values, up_to=up_to, ignore=ignore)
    except drafting.DraftError as exc:
        raise Invalid(str(exc)) from exc
    try:
        candidate = policy_store.create_candidate(
            conn,
            payload=payload,
            trace_id=trace_id,
            origin_case_id=case_ref,
            origin_decision_id=decision["decision_id"],
            actor=actor,
        )
    except PolicyValidationError as exc:
        raise Invalid("the drafted precedent is not valid", exc.errors) from exc

    # A person may automate as little as they like, so no coverage floor; zero
    # false automatic actions against every past answer is never waived.
    report = replay_candidate_policy(conn, candidate.definition, enforce_coverage=False)
    policy_store.attach_replay_report(conn, candidate.policy_id, report.to_dict(), trace_id=trace_id)
    replay = {
        "cases_replayed": report.cases_replayed,
        "false_automatic_actions": report.false_automatic_actions,
        "passed": report.passed,
    }
    try:
        active = policy_store.activate(
            conn, candidate.policy_id, approval_token=approval_token,
            activated_by=actor, trace_id=trace_id,
        )
    except policy_store.ActivationDenied as exc:
        contradictions = [
            f"{f['case_id']}: {f['scenario_note']}"
            for f in report.failures
            if f["expected"] == "escalate" and f["predicted"] == "auto_resolve"
        ]
        raise Conflict(
            f"not remembered: {exc}. {'; '.join(report.blocking_reasons)}",
            [*report.blocking_reasons, *contradictions] or [str(exc)],
        ) from exc
    return active, replay


def open_asks(conn: "db.Connection", workstream: str) -> list[dict[str, Any]]:
    """Cases still waiting, oldest first: one ask per case, unanswered, not
    resolved by a follow, and with no later decision on the same case."""
    rows = conn.execute(
        """SELECT decision_id, case_ref, verdict, resolves, created_at FROM harness_decisions
            WHERE workstream = ? ORDER BY created_at, decision_id""",
        (workstream,),
    ).fetchall()
    answered = {
        r["decision_id"]
        for r in conn.execute(
            """SELECT DISTINCT a.decision_id FROM harness_answers a
                 JOIN harness_decisions d ON d.decision_id = a.decision_id
                WHERE d.workstream = ?""",
            (workstream,),
        ).fetchall()
    }
    resolved = {r["resolves"] for r in rows if r["resolves"] and r["verdict"] == "follow"}
    newest: dict[str, str] = {}
    for row in rows:
        newest[row["case_ref"]] = max(newest.get(row["case_ref"], ""), row["created_at"])
    waiting: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["verdict"] != "ask" or row["decision_id"] in answered | resolved:
            continue
        if row["created_at"] < newest[row["case_ref"]]:
            continue  # the case was decided again later
        waiting[row["case_ref"]] = row
    return sorted(waiting.values(), key=lambda r: (r["created_at"], r["decision_id"]))


def resolve_waiting(
    contract: WorkstreamContract, precedent: policy_store.PolicyRecord, *, skip_case: str | None = None
) -> list[str]:
    """Assess the waiting asks the new precedent covers again, at most 20.

    Each goes back through the decision service on its stored facts (no new
    reader call; Sapling still runs), as a new decision that `resolves` the
    ask. The ask itself is never edited. Only asks the precedent plainly covers
    are sent, so a case it does not cover gets no extra decision.
    """
    with db.read_only() as conn:
        waiting = []
        for row in open_asks(conn, contract.id):
            if row["case_ref"] == skip_case:
                continue
            try:
                facts = cases.facts_for(conn, contract, row["case_ref"])
            except LookupError:
                continue
            _, hard = domain.guard_checks(contract, facts)
            if hard or not match_policy(precedent.definition, facts).matched:
                continue
            waiting.append(row)
            if len(waiting) >= MAX_RESOLVE:
                break
    if not waiting:
        return []
    try:
        service = importlib.import_module("app.harness.service")
    except ImportError:
        log.warning("the decision service is not available; %d waiting asks stay open", len(waiting))
        return []

    resolved: list[str] = []
    for row in waiting:
        request = DecideRequest(
            workstream=contract.id,
            case_ref=row["case_ref"],
            idempotency_key=f"resolve:{row['decision_id']}:{precedent.policy_id}",
            caller="precedenz",
        )
        try:
            receipt = service.decide(request, resolves=row["decision_id"])
        except Exception:  # one case failing must not stop the others
            log.exception("re-assessing %s failed", row["decision_id"])
            continue
        if isinstance(receipt, dict) and receipt.get("verdict") == "follow":
            resolved.append(receipt["decision_id"])
    return resolved


# ---------------------------------------------------------------- overrule


def overrule(precedent_id: str, *, reason: str, actor: str) -> dict[str, Any]:
    """Stop a precedent at once. Nothing replaces it; its cases ask again."""
    reason = (reason or "").strip()
    actor = (actor or "").strip() or "unknown"
    if not reason:
        raise Invalid("say why: an overrule needs a reason")
    with _write_session() as conn:
        try:
            policy_store.get(conn, precedent_id)
        except KeyError:
            raise NotFound(f"precedent '{precedent_id}' not found") from None
        try:
            record = policy_store.retire(
                conn, precedent_id, by=actor, reason=reason[:500], trace_id=_trace_id()
            )
        except policy_store.NotActive as exc:
            raise Conflict(str(exc)) from exc
    events.publish(
        "precedent.overruled", precedent_id=precedent_id, workstream=record.family, by=actor, reason=reason
    )
    return {
        "precedent_id": precedent_id,
        "workstream": record.family,
        "version": record.version,
        "status": record.status,
        "retired_at": record.retired_at,
        "by": actor,
        "reason": reason,
        "text": record.definition.description or record.definition.name,
    }
