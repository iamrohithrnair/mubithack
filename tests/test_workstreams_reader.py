"""The reader and Banyan Sapling on planted workstreams: if it can't check it, it asks."""

from __future__ import annotations

import dataclasses

import pytest

from app import config, sapling
from app.workstreams import cases, reader, registry, service
from app.workstreams.engine import NO_PRECEDENT
from tests.test_workstreams_support import (  # noqa: F401  (fixtures)
    APPROVAL,
    DANA,
    fake_service,
    incoming,
    isolated_registry,
    send,
    send_incoming,
    template,
)

pytestmark = pytest.mark.usefixtures("isolated_registry")

CAL = "calendar.meeting_invite"
MEAL = "expenses.meal_claim"


@pytest.fixture()
def reading(monkeypatch):
    """Replace the model call: each case's reading is whatever the test says it is."""
    state: dict = {"by_case": {}, "calls": []}

    def fake_read(conn, contract, *, case_ref, text, trace_id, calls):
        state["calls"].append(case_ref)
        value = state["by_case"].get(case_ref)
        if isinstance(value, Exception):
            raise value
        if value is None:  # by default the text says exactly what the caller sent
            value = incoming(CAL, case_ref)["facts"]
        return dict(value)

    monkeypatch.setattr(reader, "read", fake_read)
    return state


def test_a_reader_mismatch_asks_and_never_resolves_on_its_own(conn, fake_service, reading):
    service.plant(CAL, actor=DANA)
    facts = incoming(CAL, "CAL-1032")["facts"]
    reading["by_case"]["CAL-1032"] = {**facts, "has_agenda": True}

    receipt = send_incoming(fake_service, CAL, "CAL-1032")
    assert receipt["verdict"] == "ask"
    assert receipt["reasons"] == ["the meeting invite text says has_agenda is true, but the caller sent false"]
    assert cases.load(conn, CAL, "CAL-1032").source == cases.SOURCE_UNCONFIRMED

    # Assessed again on the stored facts, it still asks: unconfirmed facts never follow.
    again = send(fake_service, CAL, "CAL-1032")
    assert again["verdict"] == "ask"
    assert "the facts on file were never confirmed; send the case again" in again["reasons"]


def test_a_reader_failure_asks_with_the_reason(fake_service, reading):
    service.plant(CAL, actor=DANA)
    reading["by_case"]["CAL-1031"] = reader.ReaderError("gateway timeout")
    receipt = send_incoming(fake_service, CAL, "CAL-1031")
    assert receipt["verdict"] == "ask"
    assert receipt["reasons"] == ["could not read the meeting invite: gateway timeout"]

    reading["by_case"]["CAL-1033"] = {"organizer_external": "perhaps"}
    receipt = send_incoming(fake_service, CAL, "CAL-1033")
    assert receipt["reasons"][0].startswith("could not read the meeting invite: organizer_external")


def test_the_reader_alone_supplies_the_facts(conn, fake_service, reading):
    service.plant(CAL, actor=DANA)
    item = incoming(CAL, "CAL-1031")
    receipt = send(fake_service, CAL, "CAL-1031", text=item["text"])
    assert receipt["verdict"] == "ask" and receipt["reasons"] == [NO_PRECEDENT]
    assert receipt["facts"] == item["facts"]
    assert cases.load(conn, CAL, "CAL-1031").source == cases.SOURCE_READER

    # A reading that leaves a required field unstated asks for it.
    reading["by_case"]["CAL-1034"] = {**incoming(CAL, "CAL-1034")["facts"], "has_agenda": None}
    receipt = send(fake_service, CAL, "CAL-1034", text=incoming(CAL, "CAL-1034")["text"])
    assert receipt["reasons"] == ["required field missing: has_agenda"]


def test_calendar_decide_once_reuses_stored_facts_without_another_model_call(fake_service, reading):
    service.plant(CAL, actor=DANA)
    asks = {ref: send_incoming(fake_service, CAL, ref) for ref in ("CAL-1031", "CAL-1032", "CAL-1033", "CAL-1035")}
    assert {a["verdict"] for a in asks.values()} == {"ask"}
    assert asks["CAL-1035"]["reasons"] == ["always ask: from_manager is true"]
    calls_before = len(reading["calls"])

    out = service.answer(
        asks["CAL-1031"]["decision_id"], "decline", remember=True,
        ignore=["duration_minutes", "overlaps_focus_time"], actor=DANA, approval_token=APPROVAL,
    )
    assert out["precedent"]["text"] == (
        "Decline meeting invites when the organizer is outside the company, it has no agenda "
        "and it is not from my manager."
    )
    assert out["replay"]["passed"] and out["replay"]["false_automatic_actions"] == 0
    assert len(out["resolved"]) == 1  # CAL-1032: external, no agenda; CAL-1033 is internal
    assert len(reading["calls"]) == calls_before  # no reader call while resolving

    follow = send_incoming(fake_service, CAL, "CAL-1034")
    assert follow["verdict"] == "follow"
    assert follow["actions"] == [{"type": "answer", "answer": "decline"}]


def test_the_real_reader_under_the_test_model_fails_closed(conn):
    """The hermetic test model has no builder for a reading, so the real reader
    raises ReaderError (an ask) rather than hanging or erroring."""
    contract = registry.parse_contract(template(CAL)["contract"])
    registry.register(contract)
    with pytest.raises(reader.ReaderError):
        reader.read(conn, contract, case_ref="CAL-1031", text=incoming(CAL, "CAL-1031")["text"],
                    trace_id="trc_test", calls=None)


def test_the_reader_task_is_stable_per_workstream():
    contract = registry.parse_contract(template(CAL)["contract"])
    model = reader.output_model(contract)
    assert list(model.model_fields) == [f.name for f in contract.fields]
    assert reader.output_model(contract) is model
    prompt = reader.system_prompt(contract)
    assert prompt == reader.system_prompt(contract)
    assert "Meeting invites" in prompt and "CAL-" not in prompt and "@" not in prompt
    reading = model(organizer_external=True, has_agenda=None)
    assert reading.model_dump()["duration_minutes"] is None


# --------------------------------------------------------------- Sapling


@pytest.fixture()
def sapling_enforced(monkeypatch):
    """Sapling on, and a meals contract that asks it to confirm the claim's own text.

    The shipped meals template trusts the expense system's record (evidence "none");
    these tests plant the same contract with evidence "text" to cover the check.
    """
    original = service.load_template

    def with_evidence(template_id):
        loaded = original(template_id)
        if template_id == MEAL:
            loaded = {**loaded, "contract": {**loaded["contract"], "evidence": "text"}}
        return loaded

    monkeypatch.setattr(service, "load_template", with_evidence)
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.get_settings(), sapling_mode="enforce"))
    seen: dict = {"verdict": None, "calls": []}

    def fake_verify(policy, evidence, claim=sapling.CLAIM, *, settings=None):
        seen["calls"].append((policy, evidence))
        return seen["verdict"]

    monkeypatch.setattr(sapling, "verify", fake_verify)
    return seen


def _precedent(fake_service) -> dict:
    ask = send_incoming(fake_service, MEAL, "EXP-1031")
    return service.answer(
        ask["decision_id"], "approve", remember=True, up_to={"amount_usd": 75},
        ignore=["client_meeting", "attendees"], actor=DANA, approval_token=APPROVAL,
    )


def test_sapling_supported_lets_a_follow_through(fake_service, sapling_enforced):
    service.plant(MEAL, actor=DANA)
    sapling_enforced["verdict"] = sapling.SaplingVerdict("supported", "unsure", {"supported": 0.81})
    _precedent(fake_service)

    receipt = send_incoming(fake_service, MEAL, "EXP-1041")
    assert receipt["verdict"] == "follow"
    check = next(c for c in receipt["checks"] if c["name"] == "Banyan Sapling: evidence supports it")
    assert check["ok"] and check["detail"] == "supported, p=0.81"
    policy, evidence = sapling_enforced["calls"][-1]
    assert "Pizza for the four people" in evidence  # the caller's own text, never a reading of it
    assert "at most $75.00" in policy and "a receipt is attached" in policy


@pytest.mark.parametrize(
    "verdict, reason",
    [
        (sapling.SaplingVerdict("insufficient_information", "unsure", {"insufficient_information": 0.7}),
         "Banyan Sapling could not confirm the evidence (insufficient information)"),
        (sapling.SaplingVerdict(None, None, error="URLError: timed out"),
         "Banyan Sapling could not be reached (URLError: timed out), so it could not check the evidence"),
        (sapling.SaplingVerdict("supported", "unsure", {"supported": 0.3}),
         "Banyan Sapling supports it only weakly (p=0.30, needs 0.50)"),
    ],
)
def test_an_unsure_or_unreachable_sapling_asks(fake_service, sapling_enforced, verdict, reason):
    service.plant(MEAL, actor=DANA)
    sapling_enforced["verdict"] = sapling.SaplingVerdict("supported", "unsure", {"supported": 0.9})
    _precedent(fake_service)

    sapling_enforced["verdict"] = verdict
    receipt = send_incoming(fake_service, MEAL, "EXP-1039")  # the ambiguous drinks note
    assert receipt["verdict"] == "ask"
    assert receipt["reasons"] == [reason]
    assert receipt["precedent_id"] is not None  # the nearest precedent is named


def test_a_case_without_text_cannot_be_checked_and_asks(fake_service, sapling_enforced):
    service.plant(MEAL, actor=DANA)
    sapling_enforced["verdict"] = sapling.SaplingVerdict("supported", "unsure", {"supported": 0.9})
    _precedent(fake_service)
    receipt = send(fake_service, MEAL, "EXP-1048", facts=incoming(MEAL, "EXP-1048")["facts"])
    assert receipt["verdict"] == "ask"
    assert receipt["reasons"] == ["Banyan Sapling could not check it: meal claim EXP-1048 has no text to check"]
