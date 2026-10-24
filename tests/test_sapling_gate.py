"""The Banyan Sapling semantic check: the last gate before an approved policy acts.

The endpoint is hosted, so every test here stubs it. What is under test is the
gate: only a confident `supported` lets an action run, and everything else —
another label, a weak `supported`, an unreachable endpoint — escalates with no
business write.
"""

from __future__ import annotations

import dataclasses

import pytest

from app import audit, orchestrator, sapling
from app.audit import AuditEventType
from app.domain import Outcome
from app.domains import returns
from app.orchestrator import activate_policy, approve_and_teach, handle_event

OPERATOR = "dana.r@northgate-optics.example"


@pytest.fixture()
def taught(conn, approval_token):
    first = handle_event("CASE-2001", conn=conn)
    teach = approve_and_teach(conn, first.exception_id, decided_by=OPERATOR)
    activate_policy(conn, teach.policy_id, approval_token=approval_token, activated_by=OPERATOR)
    return teach


@pytest.fixture()
def enforced(monkeypatch):
    monkeypatch.setattr(
        orchestrator,
        "settings",
        dataclasses.replace(orchestrator.settings, sapling_mode="enforce"),
    )


def _stub(monkeypatch, verdict: sapling.SaplingVerdict) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []

    def fake_verify(policy, evidence, claim=sapling.CLAIM, *, settings=None):
        seen.append((policy, evidence))
        return verdict

    monkeypatch.setattr(sapling, "verify", fake_verify)
    return seen


def _work_orders(conn, case_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) c FROM work_orders WHERE case_id = ?", (case_id,)
    ).fetchone()["c"]


SUPPORTED = sapling.SaplingVerdict(
    "supported", "unsure", {"supported": 0.71, "violation": 0.04}, latency_ms=90
)


def test_supported_verdict_lets_the_policy_act(conn, taught, enforced, monkeypatch):
    seen = _stub(monkeypatch, SUPPORTED)

    result = handle_event("CASE-2002", conn=conn)

    assert result.outcome is Outcome.AUTO_RESOLVED
    assert _work_orders(conn, "CASE-2002") == 1
    types = [e.event_type for e in audit.read_for_case(conn, "CASE-2002")]
    assert AuditEventType.SEMANTIC_SUPPORTED in types
    assert types.index(AuditEventType.SEMANTIC_SUPPORTED) < types.index(
        AuditEventType.ACTION_EXECUTED
    )
    policy, evidence = seen[0]
    assert "at most $25.00" in policy
    assert "4100-SN-02202" in evidence


def test_note_revealing_damage_is_sent_and_a_violation_blocks(conn, taught, enforced, monkeypatch):
    seen = _stub(
        monkeypatch,
        sapling.SaplingVerdict(
            "insufficient_information", "unsure", {"supported": 0.35, "insufficient_information": 0.51}
        ),
    )

    result = handle_event("CASE-2007", conn=conn)

    assert result.outcome is Outcome.ESCALATED
    assert "Banyan Sapling" in result.escalation_reasons[0]
    assert "insufficient information" in result.escalation_reasons[0]
    assert _work_orders(conn, "CASE-2007") == 0
    assert "Battery door is scorched" in seen[0][1]
    types = [e.event_type for e in audit.read_for_case(conn, "CASE-2007")]
    assert AuditEventType.SEMANTIC_BLOCKED in types
    assert AuditEventType.ACTION_EXECUTED not in types


def test_weak_support_escalates(conn, taught, enforced, monkeypatch):
    _stub(monkeypatch, sapling.SaplingVerdict("supported", "unsure", {"supported": 0.41}))

    result = handle_event("CASE-2002", conn=conn)

    assert result.outcome is Outcome.ESCALATED
    assert "only weakly" in result.escalation_reasons[0]
    assert _work_orders(conn, "CASE-2002") == 0


def test_unreachable_endpoint_fails_closed(conn, taught, enforced, monkeypatch):
    monkeypatch.setattr(
        orchestrator,
        "settings",
        dataclasses.replace(orchestrator.settings, sapling_url="http://127.0.0.1:9/"),
    )

    result = handle_event("CASE-2002", conn=conn)

    assert result.outcome is Outcome.ESCALATED
    assert "failing closed" in result.escalation_reasons[0]
    assert _work_orders(conn, "CASE-2002") == 0


def test_off_mode_never_calls_sapling(conn, taught, monkeypatch):
    seen = _stub(monkeypatch, SUPPORTED)

    result = handle_event("CASE-2002", conn=conn)

    assert result.outcome is Outcome.AUTO_RESOLVED
    assert seen == []


def test_guardrail_escalations_never_reach_sapling(conn, taught, enforced, monkeypatch):
    """Sapling only ever sees cases the engine was about to act on."""
    seen = _stub(monkeypatch, SUPPORTED)

    result = handle_event("CASE-2003", conn=conn)

    assert result.outcome is Outcome.ESCALATED
    assert seen == []


@pytest.mark.parametrize(
    "response, label, error",
    [
        ({"raw_label": "violation", "status": "unsure", "probabilities": {"violation": 0.9}}, "violation", None),
        ({"error": "Body needs to provide a inputs key"}, None, "Body needs to provide a inputs key"),
        ({"raw_label": "maybe"}, None, "unexpected label 'maybe'"),
        ([], None, "malformed response"),
    ],
)
def test_parse_response(response, label, error):
    verdict = sapling.parse_response(response)
    assert verdict.raw_label == label
    assert verdict.error == error
    assert not verdict.passed(0.5)


def test_evidence_shows_serials_side_by_side_without_comparing(conn):
    text = returns.evidence_text(conn, "CASE-2003")
    assert "Serial expected on the order" in text
    assert "Serial read from the returned unit" in text
    assert "match" not in text.lower()
