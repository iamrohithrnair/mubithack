"""The inbox tells the live map what it decided, and nothing it does depends on that.

The inbox path keeps its own statuses, audit trail and execution (the rest of
the suite proves that); here it also publishes `decision.*` events with
`via="inbox"`, and activation publishes `precedent.decided`.
"""

from __future__ import annotations

import pytest

from app.domain import Outcome
from app.harness import events
from app.orchestrator import NO_PRECEDENT_REASON, activate_policy, approve_and_teach, handle_event
from tests.test_harness_service import OPERATOR, RETURNS, ap_precedent  # noqa: F401 - fixture


def _since(seq: int, prefix: str = "") -> list[dict]:
    return [e for e in events.since(seq) if e["type"].startswith(prefix)]


def test_a_decision_card_is_an_ask_on_the_map(conn):
    start = events.last_seq()
    result = handle_event("CASE-2001", conn=conn)

    assert result.outcome is Outcome.DECISION_REQUESTED
    started, finished = _since(start, "decision.")
    assert started["type"] == "decision.started"
    assert (started["workstream"], started["case_ref"], started["via"]) == (
        RETURNS, "CASE-2001", "inbox"
    )
    assert finished["type"] == "decision.finished"
    assert finished["decision_id"] == result.exception_id
    assert finished["verdict"] == "ask"
    assert finished["reasons"] == [NO_PRECEDENT_REASON]
    assert finished["precedent_id"] is None
    assert (finished["via"], finished["caller"], finished["resolves"]) == ("inbox", "inbox", None)


def test_activation_announces_the_precedent_then_the_case_it_resolves(conn, approval_token):
    first = handle_event("CASE-2001", conn=conn)
    teach = approve_and_teach(conn, first.exception_id, decided_by=OPERATOR)
    start = events.last_seq()

    activate_policy(conn, teach.policy_id, approval_token=approval_token, activated_by=OPERATOR)

    seen = [e for e in events.since(start) if e["type"] in ("precedent.decided", "decision.finished")]
    assert [e["type"] for e in seen] == ["precedent.decided", "decision.finished"]
    decided, resolved = seen
    assert decided["precedent_id"] == teach.policy_id
    assert decided["workstream"] == RETURNS
    assert decided["version"] == 1
    assert decided["decided_by"] == OPERATOR
    assert decided["origin_case"] == "CASE-2001"
    assert decided["text"]
    assert resolved["case_ref"] == "CASE-2001"
    assert resolved["verdict"] == "follow"
    assert resolved["precedent_id"] == teach.policy_id


@pytest.fixture()
def taught(conn, approval_token):
    first = handle_event("CASE-2001", conn=conn)
    teach = approve_and_teach(conn, first.exception_id, decided_by=OPERATOR)
    activate_policy(conn, teach.policy_id, approval_token=approval_token, activated_by=OPERATOR)
    return teach


def test_follows_and_asks_from_the_inbox(conn, taught):
    start = events.last_seq()
    handle_event("CASE-2002", conn=conn)
    handle_event("CASE-2003", conn=conn)

    finished = _since(start, "decision.finished")
    assert [(e["case_ref"], e["verdict"]) for e in finished] == [
        ("CASE-2002", "follow"), ("CASE-2003", "ask")
    ]
    assert finished[0]["precedent_id"] == taught.policy_id and finished[0]["reasons"] == []
    assert any("serial number does not match" in r for r in finished[1]["reasons"])


def test_a_duplicate_event_announces_nothing(conn, taught):
    handle_event("CASE-2002", conn=conn, event_key="evt-dup")
    start = events.last_seq()
    handle_event("CASE-2002", conn=conn, event_key="evt-dup")
    assert _since(start, "decision.") == []


def test_an_unknown_case_is_an_ask_on_the_map(conn):
    start = events.last_seq()
    handle_event("CASE-9999", conn=conn)
    (finished,) = _since(start, "decision.finished")
    assert finished["verdict"] == "ask"
    assert "unknown case 'CASE-9999'" in finished["reasons"][0]


def test_a_broken_event_bus_never_breaks_the_inbox(conn, approval_token, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("bus down")

    monkeypatch.setattr(events, "publish", broken)
    first = handle_event("CASE-2001", conn=conn)
    assert first.outcome is Outcome.DECISION_REQUESTED
    teach = approve_and_teach(conn, first.exception_id, decided_by=OPERATOR)
    activate_policy(conn, teach.policy_id, approval_token=approval_token, activated_by=OPERATOR)
    assert handle_event("CASE-2002", conn=conn).outcome is Outcome.AUTO_RESOLVED
    assert handle_event("CASE-2003", conn=conn).outcome is Outcome.ESCALATED


def test_another_workstreams_precedent_does_not_block_a_decision_card(conn, ap_precedent):  # noqa: F811
    """An active AP precedent is no boundary for a return case: it still gets a card."""
    result = handle_event("CASE-2001", conn=conn)
    assert result.outcome is Outcome.DECISION_REQUESTED
    assert result.decision_card is not None
