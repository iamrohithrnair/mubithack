"""The decision harness: repeat a decision a person made, or ask. Never act.

Every case here goes through `service.decide`, the path every caller (HTTP, CLI,
MCP, the re-assessment of waiting asks) shares. The model is the deterministic
test fake and Sapling is off unless a test stubs it, as in the rest of the suite.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime

import pytest

from app import audit, orchestrator, sapling
from app.audit import AuditEventType
from app.domains import ap_invoices as ap
from app.harness import events, service
from app.harness.engines import Assessment, Check, DecideRequest
from app.orchestrator import activate_policy
from app.policy import store
from app.policy.replay import replay_candidate_policy
from tests.conftest import DEMO_ACTIONS, demo_policy_payload

OPERATOR = "dana.r@wharf-canary-optics.example"
RETURNS = "returns.missing_accessory"


def remember(conn, approval_token, payload: dict, origin_case: str) -> str:
    """A person decides once: a candidate, its replay, and activation with the token."""
    record = store.create_candidate(
        conn, payload=payload, trace_id="trc_harness_test", origin_case_id=origin_case
    )
    report = replay_candidate_policy(conn, record.definition)
    assert report.passed, report.blocking_reasons
    store.attach_replay_report(conn, record.policy_id, report.to_dict(), trace_id="trc_harness_test")
    activate_policy(conn, record.policy_id, approval_token=approval_token, activated_by=OPERATOR)
    conn.commit()  # the service decides on its own connections
    return record.policy_id


@pytest.fixture()
def precedent(conn, approval_token) -> str:
    return remember(conn, approval_token, demo_policy_payload(), "CASE-2001")


@pytest.fixture()
def ap_precedent(conn, approval_token) -> str:
    payload = {
        "family": ap.FAMILY,
        "name": "Small clean price variance on a matched purchase order",
        "description": "Pay a variance up to $75 and 2% when the PO, receipt and tax all reconcile.",
        "conditions": [
            {"field": "variance_usd", "operator": "lte", "value": 75},
            {"field": "variance_pct", "operator": "lte", "value": 2},
            {"field": "po_match", "operator": "eq", "value": True},
            {"field": "receipt_match", "operator": "eq", "value": True},
            {"field": "tax_consistent", "operator": "eq", "value": True},
            {"field": "duplicate_suspected", "operator": "eq", "value": False},
            {"field": "vendor_on_hold", "operator": "eq", "value": False},
        ],
        "actions": ap.fixed_actions(75.0),
        "min_confidence": 0.75,
    }
    return remember(conn, approval_token, payload, "AP-2001")


def decide(case_ref: str, *, workstream: str = RETURNS, key: str | None = None, **kw) -> dict:
    return service.decide(
        DecideRequest(
            workstream=workstream,
            case_ref=case_ref,
            idempotency_key=key or f"test::{workstream}::{case_ref}",
            **kw,
        )
    )


def business_writes(conn) -> dict[str, int]:
    """Row counts of every table an action could write, the inbox's own included."""
    tables = (
        "work_orders",
        "dispositions",
        "ap_adjustments",
        "ap_payment_releases",
        "exceptions",
        "decision_cards",
    )
    return {
        table: conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"] for table in tables
    }


# ------------------------------------------------------------------ follow


def test_a_matching_case_follows_the_precedent(conn, precedent):
    receipt = decide("CASE-2002")

    assert receipt["verdict"] == "follow"
    assert receipt["reasons"] == []
    assert receipt["actions"] == DEMO_ACTIONS
    assert receipt["precedent"] == {
        "id": precedent,
        "version": store.get(conn, precedent).version,
        "text": demo_policy_payload()["description"],
        "decided_by": OPERATOR,
        "decided_at": store.get(conn, precedent).activated_at,
        "origin_case": "CASE-2001",
    }
    assert receipt["checks"] and all(c["ok"] for c in receipt["checks"])
    names = [c["name"] for c in receipt["checks"]]
    assert "hard guardrails" in names
    assert any(n.startswith("matches precedent v") for n in names)
    sapling_check = next(c for c in receipt["checks"] if c["name"].startswith("Banyan Sapling"))
    assert sapling_check["detail"].startswith("not run")  # Sapling is off in tests, and says so


def test_a_receipt_carries_its_provenance(conn, precedent):
    receipt = decide("CASE-2002", source_revision="r3")

    assert receipt["decision_id"].startswith("dec_")
    assert receipt["workstream"] == RETURNS and receipt["case_ref"] == "CASE-2002"
    assert receipt["serving_identity"] == "not reported by the gateway"
    assert receipt["input_hash"].startswith("sha256:")
    assert receipt["source_revision"] == "r3"
    assert receipt["model_calls"] == []  # the test fake makes no gateway call
    # Set by the agent build when it is handed the decision's CallLog (Mubit wiring).
    assert receipt["task_key"] is None or isinstance(receipt["task_key"], str)
    assert receipt["resolves"] is None
    assert receipt["caller"] == "api"
    created = datetime.fromisoformat(receipt["created_at"])
    expires = datetime.fromisoformat(receipt["expires_at"])
    assert (expires - created).total_seconds() == 15 * 60


def test_gateway_calls_and_the_task_key_land_on_the_receipt(conn, precedent, monkeypatch):
    """The decision's CallLog reaches the investigation; what it records reaches the receipt."""
    from app.agent.providers import ModelCall

    real = orchestrator._run_investigation

    def through_the_gateway(ctx, case_id):
        outcome = real(ctx, case_id)
        assert (ctx.call_log.family, ctx.call_log.case_ref) == (RETURNS, "CASE-2002")
        ctx.call_log.append(
            ModelCall(request_id="mreq_1", requested_model="gpt-6-luna", status=200, ms=2140)
        )
        ctx.call_log.task_key = "7f3a9c"
        return outcome

    monkeypatch.setattr(orchestrator, "_run_investigation", through_the_gateway)
    receipt = decide("CASE-2002")

    assert receipt["model_calls"] == [
        {"via": "mubit", "request_id": "mreq_1", "requested_model": "gpt-6-luna",
         "status": 200, "ms": 2140}
    ]
    assert receipt["task_key"] == "7f3a9c"
    (entry,) = [
        e for e in audit.read_for_case(conn, "CASE-2002")
        if e.event_type == AuditEventType.HARNESS_DECIDED
    ]
    assert entry.payload["model_request_ids"] == ["mreq_1"]


def test_a_follow_writes_nothing_to_any_business_system(conn, precedent):
    before = business_writes(conn)
    assert decide("CASE-2002")["verdict"] == "follow"
    assert business_writes(conn) == before


def test_every_decision_is_audited_once(conn, precedent):
    receipt = decide("CASE-2002")
    entries = [
        e for e in audit.read_for_case(conn, "CASE-2002")
        if e.event_type == AuditEventType.HARNESS_DECIDED
    ]
    assert len(entries) == 1
    assert entries[0].payload["decision_id"] == receipt["decision_id"]
    assert entries[0].payload["verdict"] == "follow"
    assert entries[0].policy_id == precedent
    assert audit.verify_chain(conn).ok


# --------------------------------------------------------------------- ask


@pytest.mark.parametrize(
    "case_ref,fragment",
    [
        ("CASE-2003", "serial number does not match"),
        ("CASE-2004", "replacement_cost_usd"),
        ("CASE-2006", "tool failure"),
    ],
)
def test_a_case_that_differs_asks_with_the_reason(conn, precedent, case_ref, fragment):
    before = business_writes(conn)
    receipt = decide(case_ref)

    assert receipt["verdict"] == "ask"
    assert receipt["actions"] == []
    assert any(fragment in r for r in receipt["reasons"]), receipt["reasons"]
    assert any(not c["ok"] for c in receipt["checks"])
    assert receipt["precedent"]["id"] == precedent  # the nearest precedent, for the person
    assert business_writes(conn) == before


def test_with_no_precedent_a_person_decides_once(conn):
    receipt = decide("CASE-2001")

    assert receipt["verdict"] == "ask"
    assert receipt["reasons"] == ["no precedent yet: a person decides once"]
    assert receipt["precedent"] is None
    # The harness writes no exception and no decision card; the inbox owns those.
    assert conn.execute("SELECT COUNT(*) c FROM exceptions").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM decision_cards").fetchone()["c"] == 0


def test_an_unknown_case_asks(conn, precedent):
    receipt = decide("CASE-9999")
    assert receipt["verdict"] == "ask"
    assert "unknown case 'CASE-9999'" in receipt["reasons"][0]


def test_a_model_failure_asks(conn, precedent, monkeypatch):
    def blow_up(ctx, case_id):
        return orchestrator._AgentOutcome(None, "TimeoutError: model call timed out", [], [])

    monkeypatch.setattr(orchestrator, "_run_investigation", blow_up)
    receipt = decide("CASE-2002")
    assert receipt["verdict"] == "ask"
    assert "model call timed out" in receipt["reasons"][0]


def test_an_engine_exception_asks_with_its_type_and_message(conn, precedent, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("the returns system went away")

    monkeypatch.setattr(orchestrator, "assess_case", broken)
    receipt = decide("CASE-2002")
    assert receipt["verdict"] == "ask"
    assert receipt["reasons"] == ["RuntimeError: the returns system went away"]
    assert receipt["actions"] == []


@pytest.fixture()
def enforced(monkeypatch):
    monkeypatch.setattr(
        orchestrator,
        "settings",
        dataclasses.replace(orchestrator.settings, sapling_mode="enforce"),
    )


def _stub_sapling(monkeypatch, verdict: sapling.SaplingVerdict) -> list:
    seen = []

    def fake_verify(policy, evidence, claim=sapling.CLAIM, *, settings=None):
        seen.append(evidence)
        return verdict

    monkeypatch.setattr(sapling, "verify", fake_verify)
    return seen


def test_what_only_sapling_can_read_asks(conn, precedent, enforced, monkeypatch):
    """CASE-2007: every structured field is clean; the note describes heat damage."""
    _stub_sapling(
        monkeypatch, sapling.SaplingVerdict("violation", "unsure", {"violation": 0.83})
    )
    receipt = decide("CASE-2007")

    assert receipt["verdict"] == "ask"
    assert "Banyan Sapling" in receipt["reasons"][0]
    check = next(c for c in receipt["checks"] if c["name"].startswith("Banyan Sapling"))
    assert check == {
        "name": "Banyan Sapling: evidence supports it",
        "ok": False,
        "detail": "violation, p=0.83",
    }


def test_a_sapling_outage_asks_when_sapling_is_enforced(conn, precedent, enforced, monkeypatch):
    _stub_sapling(
        monkeypatch, sapling.SaplingVerdict(None, None, error="URLError: connection refused")
    )
    receipt = decide("CASE-2002")
    assert receipt["verdict"] == "ask"
    assert "failing closed" in receipt["reasons"][0]


def test_a_supported_verdict_is_on_the_receipt(conn, precedent, enforced, monkeypatch):
    _stub_sapling(
        monkeypatch, sapling.SaplingVerdict("supported", "unsure", {"supported": 0.71})
    )
    receipt = decide("CASE-2002")
    assert receipt["verdict"] == "follow"
    check = next(c for c in receipt["checks"] if c["name"].startswith("Banyan Sapling"))
    assert check == {"name": "Banyan Sapling: evidence supports it", "ok": True,
                     "detail": "supported, p=0.71"}


# ---------------------------------------------------- caller mistakes


def test_an_unknown_workstream_is_refused(conn):
    with pytest.raises(service.UnknownWorkstream):
        decide("X-1", workstream="nobody.planted_this")


def test_the_same_key_and_input_returns_the_first_receipt(conn, precedent):
    first = decide("CASE-2002", key="evt-88121")
    again = decide("CASE-2002", key="evt-88121")

    assert again == first
    assert conn.execute("SELECT COUNT(*) c FROM harness_decisions").fetchone()["c"] == 1


def test_the_same_key_with_a_different_input_conflicts(conn, precedent):
    first = decide("CASE-2002", key="evt-88121", source_revision="r3")
    with pytest.raises(service.IdempotencyConflict) as exc:
        decide("CASE-2002", key="evt-88121", source_revision="r4")
    assert exc.value.decision_id == first["decision_id"]
    with pytest.raises(service.IdempotencyConflict):
        decide("CASE-2003", key="evt-88121")


def test_a_repeat_does_not_assess_again(conn, precedent, monkeypatch):
    decide("CASE-2002", key="evt-once")
    calls = []
    monkeypatch.setattr(orchestrator, "assess_case", lambda *a, **k: calls.append(a))
    decide("CASE-2002", key="evt-once")
    assert calls == []


# --------------------------------------------------------- the no-write rule


def test_the_decision_path_never_executes_verifies_or_writes(conn, precedent, ap_precedent, monkeypatch):
    """Every way to act on a business system raises. Deciding must not notice."""
    from app import domains
    from app.adapters import ap_ledger, warehouse
    from app.domains import returns

    touched: list[str] = []

    def forbidden(name):
        def boom(*a, **k):
            touched.append(name)
            raise AssertionError(f"the decision path called {name}")

        return boom

    for module, names in (
        (warehouse, ("set_disposition", "create_work_order")),
        (ap_ledger, ("post_adjustment", "release_payment")),
        (returns, ("execute", "verify")),
        (ap, ("execute", "verify")),
        (orchestrator, ("execute_approved_policy", "verify_action", "_ensure_exception",
                        "_set_status", "_build_decision_card")),
    ):
        for name in names:
            monkeypatch.setattr(module, name, forbidden(f"{module.__name__}.{name}"))
    for family in (returns.FAMILY, ap.FAMILY):
        spec = domains.get(family)
        monkeypatch.setitem(
            domains._REGISTRY,
            family,
            dataclasses.replace(
                spec, execute=forbidden(f"{family}.execute"), verify=forbidden(f"{family}.verify")
            ),
        )

    before = business_writes(conn)
    verdicts = {
        case: decide(case, workstream=family)["verdict"]
        for family, case in (
            (RETURNS, "CASE-2002"),
            (RETURNS, "CASE-2003"),
            (RETURNS, "CASE-2006"),
            (ap.FAMILY, "AP-2002"),
            (ap.FAMILY, "AP-2003"),
        )
    }

    assert verdicts == {
        "CASE-2002": "follow",
        "CASE-2003": "ask",
        "CASE-2006": "ask",
        "AP-2002": "follow",
        "AP-2003": "ask",
    }
    assert touched == []
    assert business_writes(conn) == before


# ---------------------------------------- follow invariants, whatever the engine


class _ClaimsFollow:
    """An engine that says follow, to prove the service holds it to the rules."""

    family = RETURNS

    def __init__(self, **assessment):
        self.assessment = Assessment(verdict="follow", **assessment)

    def assess(self, conn, request, *, trace_id, calls):
        return self.assessment


@pytest.fixture()
def claims_follow(monkeypatch):
    from app.harness import bootstrap, engines

    def install(**assessment):
        bootstrap.load()  # so a first load cannot replace the stand-in afterwards
        monkeypatch.setitem(engines._ENGINES, RETURNS, _ClaimsFollow(**assessment))

    return install


def test_a_follow_without_a_precedent_is_refused(conn, claims_follow):
    claims_follow(actions=({"type": "create_work_order", "max_cost_usd": 9999.0},))
    receipt = decide("CASE-2002")
    assert receipt["verdict"] == "ask"
    assert receipt["actions"] == []
    assert "a follow needs a precedent" in receipt["reasons"][-1]


def test_a_follow_with_a_failed_check_is_refused(conn, precedent, claims_follow):
    claims_follow(precedent_id=precedent, checks=(Check("hard guardrails", False, "no"),))
    receipt = decide("CASE-2002")
    assert receipt["verdict"] == "ask"
    assert "failed: hard guardrails" in receipt["reasons"][-1]


def test_a_follow_on_a_precedent_nobody_activated_is_refused(conn, claims_follow):
    candidate = store.create_candidate(
        conn, payload=demo_policy_payload(), trace_id="trc_harness_test"
    )
    claims_follow(precedent_id=candidate.policy_id)
    receipt = decide("CASE-2002")
    assert receipt["verdict"] == "ask"
    assert "not active" in receipt["reasons"][-1]


def test_a_follow_carries_the_precedents_actions_never_the_engines(conn, precedent, claims_follow):
    claims_follow(
        precedent_id=precedent,
        actions=({"type": "create_work_order", "max_cost_usd": 9999.0},),
    )
    receipt = decide("CASE-2002")
    assert receipt["verdict"] == "follow"
    assert receipt["actions"] == DEMO_ACTIONS


# --------------------------------------------------------------- AP engine


def test_ap_follows_its_precedent_without_a_model(conn, ap_precedent, monkeypatch):
    from app.agent import build

    def no_model(*a, **k):
        raise AssertionError("the AP engine must not call a model")

    monkeypatch.setattr(build, "resolve_model", no_model)
    receipt = decide("AP-2002", workstream=ap.FAMILY)

    assert receipt["verdict"] == "follow"
    assert receipt["precedent"]["id"] == ap_precedent
    assert receipt["actions"] == ap.fixed_actions(75.0)
    assert receipt["model_calls"] == []


@pytest.mark.parametrize(
    "invoice,fragment",
    [("AP-2003", "possible duplicate"), ("AP-2004", "exceeds the hard ceiling")],
)
def test_ap_asks_for_what_its_guardrails_stop(conn, ap_precedent, invoice, fragment):
    receipt = decide(invoice, workstream=ap.FAMILY)
    assert receipt["verdict"] == "ask"
    assert any(fragment in r for r in receipt["reasons"]), receipt["reasons"]


def test_ap_without_a_precedent_asks(conn):
    receipt = decide("AP-2002", workstream=ap.FAMILY)
    assert receipt["verdict"] == "ask"
    assert receipt["reasons"] == ["no precedent yet: a person decides once"]


def test_ap_unknown_invoice_asks(conn, ap_precedent):
    receipt = decide("AP-9999", workstream=ap.FAMILY)
    assert receipt["verdict"] == "ask"
    assert "not found in the AP ledger" in receipt["reasons"][0]


def test_one_workstreams_precedent_never_answers_for_another(conn, ap_precedent):
    receipt = decide("CASE-2002")
    assert receipt["verdict"] == "ask"
    assert receipt["reasons"] == ["no precedent yet: a person decides once"]


# -------------------------------------------- open questions, answers, outcomes


def _answer(conn, decision_id: str, answer: str = "approve") -> None:
    conn.execute(
        """INSERT INTO harness_answers (answer_id, decision_id, answer, remembered, actor, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (f"ans_{decision_id}", decision_id, answer, False, OPERATOR, "2026-10-25T01:00:00.000+00:00"),
    )
    conn.commit()


def _open_ids(workstream: str | None = None) -> set[str]:
    return {q["decision_id"] for q in service.open_questions(workstream)}


def test_open_questions_are_the_unanswered_asks(conn, approval_token):
    """Ask, a person decides once, and the waiting question it covers resolves."""
    teachable = decide("CASE-2001")  # no precedent yet
    serial = decide("CASE-2003")
    damage = decide("CASE-2005")
    assert {teachable["verdict"], serial["verdict"], damage["verdict"]} == {"ask"}
    assert _open_ids() == {teachable["decision_id"], serial["decision_id"], damage["decision_id"]}
    assert _open_ids("ap.invoice_variance") == set()

    remember(conn, approval_token, demo_policy_payload(), "CASE-2001")
    resolution = service.decide(
        DecideRequest(workstream=RETURNS, case_ref="CASE-2001", idempotency_key="reassess-2001"),
        resolves=teachable["decision_id"],
    )
    assert resolution["verdict"] == "follow"
    assert resolution["resolves"] == teachable["decision_id"]
    assert decide("CASE-2002")["verdict"] == "follow"  # a follow is never a question

    _answer(conn, serial["decision_id"])
    assert _open_ids(RETURNS) == {damage["decision_id"]}

    answered = service.get_receipt(serial["decision_id"])
    assert [a["answer"] for a in answered["answers"]] == ["approve"]
    assert answered["resolved_by"] is None
    assert service.get_receipt(teachable["decision_id"])["resolved_by"] == resolution["decision_id"]


def test_a_receipt_never_changes(conn, precedent):
    receipt = decide("CASE-2003")
    _answer(conn, receipt["decision_id"])
    service.report_outcome(receipt["decision_id"], "executed", "done by hand", "ops")

    later = service.get_receipt(receipt["decision_id"])
    assert {k: later[k] for k in receipt} == receipt
    assert len(later["answers"]) == 1 and len(later["outcomes"]) == 1


def test_an_outcome_is_appended_as_reported(conn, precedent):
    receipt = decide("CASE-2002")
    outcome = service.report_outcome(receipt["decision_id"], "executed", "WO-1 raised", "warehouse-bot")

    assert outcome["status"] == "executed"
    assert outcome["verification"] == "reported"
    assert service.get_receipt(receipt["decision_id"])["outcomes"] == [outcome]
    types = [e.event_type for e in audit.read_for_case(conn, "CASE-2002")]
    assert AuditEventType.HARNESS_OUTCOME_REPORTED in types

    with pytest.raises(service.UnknownDecision):
        service.report_outcome("dec_missing", "executed", "", "x")
    with pytest.raises(ValueError):
        service.report_outcome(receipt["decision_id"], "approved", "", "x")


def test_get_receipt_of_an_unknown_decision_is_none(conn):
    assert service.get_receipt("dec_missing") is None


# ------------------------------------------------------------------ events


def test_a_decision_is_announced_before_and_after(conn, precedent):
    start = events.last_seq()
    receipt = decide("CASE-2003")
    seen = [e for e in events.since(start) if e["type"].startswith("decision.")]

    assert [e["type"] for e in seen] == ["decision.started", "decision.finished"]
    assert seen[0]["workstream"] == RETURNS and seen[0]["case_ref"] == "CASE-2003"
    finished = seen[1]
    assert finished["decision_id"] == receipt["decision_id"]
    assert finished["verdict"] == "ask"
    assert finished["precedent_id"] == precedent
    assert finished["reasons"] == receipt["reasons"]
    assert finished["resolves"] is None and finished["caller"] == "api"


def test_a_repeat_is_not_announced_again(conn, precedent):
    decide("CASE-2002", key="evt-quiet")
    start = events.last_seq()
    decide("CASE-2002", key="evt-quiet")
    assert [e for e in events.since(start) if e["type"].startswith("decision.")] == []


def test_an_outcome_is_announced(conn, precedent):
    receipt = decide("CASE-2002")
    start = events.last_seq()
    service.report_outcome(receipt["decision_id"], "failed", "warehouse offline", "bot")
    assert [(e["type"], e["decision_id"], e["status"]) for e in events.since(start)] == [
        ("outcome.reported", receipt["decision_id"], "failed")
    ]
