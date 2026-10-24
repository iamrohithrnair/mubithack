"""Planted workstreams and the decide-once loop.

plant -> ask -> answer and remember -> follow -> overrule, on the meal-claims
template, through the planted engine and a stand-in for the decision service.
"""

from __future__ import annotations

import json

import pytest

from app.harness import engines, events
from app.workstreams import drafting, registry, service
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

MEAL = "expenses.meal_claim"
EXPECTED_TEXT = "Approve meal claims up to $75.00 when a receipt is attached and it is not alcohol only."


def plant_meals() -> dict:
    return service.plant(MEAL, actor=DANA)


def remember_demo(ask_id: str, **overrides) -> dict:
    kwargs = dict(
        remember=True,
        up_to={"amount_usd": 75},
        ignore=["client_meeting", "attendees"],
        actor=DANA,
        approval_token=APPROVAL,
    )
    kwargs.update(overrides)
    return service.answer(ask_id, "approve", **kwargs)


# ------------------------------------------------------------- contracts


def _contract(**changes) -> dict:
    contract = template(MEAL)["contract"]
    contract.update(changes)
    return contract


@pytest.mark.parametrize(
    "contract, needle",
    [
        (_contract(id="returns.missing_accessory"), "built-in"),
        (_contract(id="Not An Id"), "id"),
        (_contract(other_answers=["approve"]), "must all differ"),
        (_contract(fields=[{"name": "kind_of_meal", "kind": "category", "label": "Kind"}]), "must list its values"),
        (_contract(fields=[{"name": "total_usd", "kind": "amount", "label": "Total"}]), "needs a ceiling"),
        (_contract(fields=[{"name": "has_receipt", "kind": "flag", "label": "Receipt", "ceiling": 3}]), "ceiling"),
        (_contract(always_ask=[{"field": "no_such_field", "operator": "eq", "value": True}]), "not allowlisted"),
        (_contract(always_ask=[{"field": "alcohol_only", "operator": "gt", "value": 1}]), "not allowed"),
        (_contract(always_ask=[{"field": "amount_usd", "operator": "eq", "value": "lots"}]), "numeric"),
        # A built-in already owns `serial_match` as a flag; the policy language
        # validates fields globally, so the same name as an amount would clash.
        (_contract(fields=[{"name": "serial_match", "kind": "amount", "label": "Serial", "ceiling": 5}],
                   always_ask=[]), "already used"),
    ],
)
def test_bad_contracts_are_rejected_and_leave_nothing_registered(contract, needle):
    from app import domains

    domains.load()
    before = set(domains.families())
    with pytest.raises(service.Invalid) as exc:
        service.plant(contract=contract, actor=DANA)
    assert needle in " ".join(exc.value.errors), exc.value.errors
    assert set(domains.families()) == before


def test_plant_needs_exactly_one_source_and_a_known_template():
    with pytest.raises(service.Invalid):
        service.plant(actor=DANA)
    with pytest.raises(service.Invalid):
        service.plant(MEAL, contract=template(MEAL)["contract"], actor=DANA)
    with pytest.raises(service.NotFound):
        service.plant("expenses.unheard_of", actor=DANA)


def test_templates_are_listed_and_labeled_synthetic():
    listed = {t["id"]: t for t in service.list_templates()}
    assert set(listed) == {MEAL, "calendar.meeting_invite", "it.access_request"}
    assert listed["calendar.meeting_invite"]["audience"] == "individual"
    for template_id in listed:
        data = template(template_id)
        assert "Synthetic" in data["_notice"]
        assert len(data["history"]) == 12 and len(data["incoming"]) == 20
        registry.parse_contract(data["contract"])  # every template contract is valid


# ----------------------------------------------------------------- plant


def test_plant_from_template_registers_spec_engine_and_history(conn):
    from app import domains

    seq = events.last_seq()
    planted = plant_meals()

    assert planted["kind"] == "planted" and planted["history_imported"] == 12
    spec = domains.get(MEAL)
    assert spec.allowed_fields == {"amount_usd", "receipt_attached", "alcohol_only", "client_meeting", "attendees"}
    assert spec.numeric_ceilings == {"amount_usd": 200.0, "attendees": 12.0}
    assert spec.fixed_actions(75) == [{"type": "answer", "answer": "approve", "max_amount_usd": 75.0}]
    with pytest.raises(NotImplementedError, match="the caller executes"):
        spec.execute()
    assert engines.engine_for(MEAL).family == MEAL

    corpus = spec.corpus(conn)
    assert len(corpus) == 12
    assert {label for _, label, _ in corpus} == {"auto_resolve", "escalate"}

    planted_events = [e for e in events.since(seq) if e["type"] == "workstream.planted"]
    assert planted_events and planted_events[-1]["workstream"] == MEAL
    assert planted_events[-1]["audience"] == "team"

    listed = {w["id"]: w for w in service.list_workstreams()}
    assert listed["returns.missing_accessory"]["kind"] == "built-in"
    assert listed["returns.missing_accessory"]["repeatable_answer"] == "replace the missing part"
    assert listed["ap.invoice_variance"]["reader"] is False
    meals = listed[MEAL]
    assert [f["name"] for f in meals["fields"]][:2] == ["amount_usd", "receipt_attached"]
    assert meals["fields"][0] == {"name": "amount_usd", "kind": "amount", "label": "Amount (USD)",
                                  "ceiling": 200.0, "required": True}
    assert meals["evidence"] == "none" and meals["reader"] is False

    # Planting the same contract again changes nothing; a different one conflicts.
    again = plant_meals()
    assert again["already_planted"] is True and again["history_imported"] == 0
    assert len(spec.corpus(conn)) == 12
    changed = template(MEAL)["contract"]
    changed["label"] = "Meal claims, renamed"
    with pytest.raises(service.Conflict):
        service.plant(contract=changed, actor=DANA)


def test_load_all_registers_stored_workstreams_after_a_restart():
    from app import domains

    plant_meals()
    registry.unregister(MEAL)
    assert MEAL not in domains.families()
    assert registry.load_all() == [MEAL]
    assert MEAL in domains.families() and engines.engine_for(MEAL).family == MEAL


# ---------------------------------------------------------------- engine


def test_engine_asks_for_a_missing_field_a_ceiling_and_an_always_ask_rule(fake_service):
    plant_meals()
    item = incoming(MEAL, "EXP-1031")

    missing = dict(item["facts"])
    del missing["receipt_attached"]
    receipt = send(fake_service, MEAL, "EXP-1031", facts=missing, text=item["text"])
    assert receipt["verdict"] == "ask"
    assert "required field missing: receipt_attached" in receipt["reasons"]

    receipt = send_incoming(fake_service, MEAL, "EXP-1032", amount_usd=250)
    assert receipt["verdict"] == "ask"
    assert "amount_usd 250.00 is above the ceiling 200.00" in receipt["reasons"]
    assert "always ask: amount_usd > 150.00" in receipt["reasons"]

    receipt = send_incoming(fake_service, MEAL, "EXP-1034")
    assert receipt["reasons"] == ["always ask: alcohol_only is true"]

    receipt = send_incoming(fake_service, MEAL, "EXP-1033", attendees="several")
    assert receipt["verdict"] == "ask" and "attendees must be a number" in receipt["reasons"][0]


def test_engine_asks_without_a_precedent_and_follows_a_matching_one(fake_service):
    plant_meals()
    first = send_incoming(fake_service, MEAL, "EXP-1031")
    assert first["verdict"] == "ask" and first["reasons"] == [NO_PRECEDENT]
    checks = {c["name"]: c["ok"] for c in first["checks"]}
    assert checks["required fields present"] and checks["always-ask rules"] and checks["within ceilings"]

    remember_demo(first["decision_id"])
    follow = send_incoming(fake_service, MEAL, "EXP-1041")
    assert follow["verdict"] == "follow" and follow["reasons"] == []
    assert follow["actions"] == [{"type": "answer", "answer": "approve", "max_amount_usd": 75}]
    names = [c["name"] for c in follow["checks"]]
    assert "matches precedent v1" in names
    # Meals trust the expense system's record (evidence "none"), so no Sapling check runs.
    assert not any(n.startswith("Banyan Sapling") for n in names)

    near = send_incoming(fake_service, MEAL, "EXP-1035")
    assert near["verdict"] == "ask"
    assert near["reasons"] == ["amount_usd 140.00 is above the precedent's 75.00"]
    assert near["precedent_id"] == follow["precedent_id"]

    no_receipt = send_incoming(fake_service, MEAL, "EXP-1037")
    assert no_receipt["reasons"] == ["receipt_attached is false, not the precedent's true"]


# -------------------------------------------------------------- drafting


def test_drafts_follow_section_5_3():
    contract = registry.parse_contract(template(MEAL)["contract"])
    registry.register(contract)
    facts = incoming(MEAL, "EXP-1031")["facts"]  # $48.60, receipt, food, client, 2 attendees

    exact, cap = drafting.draft_conditions(contract, facts)
    assert exact == [
        {"field": "amount_usd", "operator": "lte", "value": 48.6},
        {"field": "receipt_attached", "operator": "eq", "value": True},
        {"field": "alcohol_only", "operator": "eq", "value": False},
        {"field": "client_meeting", "operator": "eq", "value": True},
        {"field": "attendees", "operator": "eq", "value": 2},
    ]
    assert cap == 48.6

    wider, cap = drafting.draft_conditions(
        contract, facts, up_to={"amount_usd": 75, "attendees": 6}, ignore=["client_meeting"]
    )
    assert wider == [
        {"field": "amount_usd", "operator": "lte", "value": 75},
        {"field": "receipt_attached", "operator": "eq", "value": True},
        {"field": "alcohol_only", "operator": "eq", "value": False},
        {"field": "attendees", "operator": "lte", "value": 6},
    ]
    assert cap == 75

    # up_to below what was observed never narrows the precedent below the case.
    floor, _ = drafting.draft_conditions(contract, facts, up_to={"amount_usd": 10})
    assert floor[0] == {"field": "amount_usd", "operator": "lte", "value": 48.6}

    # "The amount doesn't matter" means up to the workstream's ceiling: a follow always has a cap.
    anything, cap = drafting.draft_conditions(contract, facts, ignore=["amount_usd"])
    assert anything[0] == {"field": "amount_usd", "operator": "lte", "value": 200} and cap == 200

    # Always-ask rules never become part of a precedent.
    assert all(c["operator"] != "gt" for c in exact + wider + anything)

    for bad in (
        {"up_to": {"amount_usd": 500}},
        {"up_to": {"receipt_attached": 1}},
        {"ignore": ["no_such_field"]},
        {"up_to": {"attendees": 4}, "ignore": ["attendees"]},
        {"ignore": ["receipt_attached", "alcohol_only", "client_meeting", "attendees", "amount_usd"],
         "up_to": {}},
    ):
        if bad.get("ignore") and len(bad["ignore"]) == 5:
            # Ignoring everything still leaves the capped amount: valid, not an error.
            conditions, _ = drafting.draft_conditions(contract, facts, **bad)
            assert conditions == [{"field": "amount_usd", "operator": "lte", "value": 200}]
            continue
        with pytest.raises(drafting.DraftError):
            drafting.draft_conditions(contract, facts, **bad)

    payload, definition = drafting.draft(
        contract, facts, up_to={"amount_usd": 75}, ignore=["client_meeting", "attendees"]
    )
    assert definition.description == EXPECTED_TEXT
    assert payload["actions"] == [{"type": "answer", "answer": "approve", "max_amount_usd": 75.0}]


# ---------------------------------------------------------------- answer


def test_an_answer_without_remember_is_recorded_and_nothing_else(conn, fake_service):
    plant_meals()
    ask = send_incoming(fake_service, MEAL, "EXP-1037")
    seq = events.last_seq()

    out = service.answer(ask["decision_id"], "ask_for_receipt", note="please attach it",
                         actor=DANA, approval_token=APPROVAL)
    assert out["precedent"] is None and out["replay"] is None and out["resolved"] == []

    row = conn.execute("SELECT * FROM harness_answers WHERE answer_id = ?", (out["answer_id"],)).fetchone()
    assert row["answer"] == "ask_for_receipt" and not row["remembered"] and row["actor"] == DANA
    assert conn.execute("SELECT COUNT(*) AS c FROM policies WHERE family = ?", (MEAL,)).fetchone()["c"] == 0
    answered = [e for e in events.since(seq) if e["type"] == "case.answered"]
    assert answered[-1]["decision_id"] == ask["decision_id"] and answered[-1]["remembered"] is False

    with pytest.raises(service.Invalid, match="not an answer"):
        service.answer(ask["decision_id"], "maybe", actor=DANA, approval_token=APPROVAL)
    with pytest.raises(service.Invalid, match="only 'approve' can be remembered"):
        service.answer(ask["decision_id"], "decline", remember=True, actor=DANA, approval_token=APPROVAL)
    with pytest.raises(service.Forbidden):
        service.answer(ask["decision_id"], "decline", actor=DANA, approval_token="not-the-token")
    with pytest.raises(service.NotFound):
        service.answer("dec_missing", "decline", actor=DANA, approval_token=APPROVAL)


def test_a_contradiction_in_history_blocks_activation_and_records_nothing(conn, fake_service):
    plant_meals()
    ask = send_incoming(fake_service, MEAL, "EXP-1031")

    # EXP-0907, a $95.40 team dinner, was declined. "Approve up to $100" contradicts it.
    with pytest.raises(service.Conflict) as exc:
        remember_demo(ask["decision_id"], up_to={"amount_usd": 100})
    assert any("EXP-0907" in e for e in exc.value.errors), exc.value.errors
    assert conn.execute("SELECT COUNT(*) AS c FROM policies WHERE family = ?", (MEAL,)).fetchone()["c"] == 0
    assert conn.execute(
        "SELECT COUNT(*) AS c FROM harness_answers WHERE decision_id = ?", (ask["decision_id"],)
    ).fetchone()["c"] == 0


def test_a_case_behind_a_hard_rule_cannot_become_a_precedent(fake_service):
    plant_meals()
    ask = send_incoming(fake_service, MEAL, "EXP-1034")  # alcohol only
    with pytest.raises(service.Conflict, match="cannot become a precedent"):
        service.answer(ask["decision_id"], "approve", remember=True, actor=DANA, approval_token=APPROVAL)


def test_remember_resolves_the_waiting_asks_it_covers(conn, fake_service):
    plant_meals()
    asks = {
        ref: send_incoming(fake_service, MEAL, ref)
        for ref in ("EXP-1031", "EXP-1032", "EXP-1033", "EXP-1034", "EXP-1035", "EXP-1036", "EXP-1037")
    }
    assert all(a["verdict"] == "ask" for a in asks.values())
    seq = events.last_seq()

    out = remember_demo(asks["EXP-1031"]["decision_id"], note="client meeting doesn't matter")

    assert out["precedent"]["version"] == 1 and out["precedent"]["text"] == EXPECTED_TEXT
    assert out["replay"]["passed"] and out["replay"]["false_automatic_actions"] == 0
    assert out["replay"]["cases_replayed"] == 13  # 12 past answers and this one

    resolved = {
        r["case_ref"]: r
        for r in conn.execute(
            "SELECT * FROM harness_decisions WHERE resolves IS NOT NULL ORDER BY created_at"
        ).fetchall()
    }
    assert set(resolved) == {"EXP-1032", "EXP-1033", "EXP-1036"}
    assert sorted(out["resolved"]) == sorted(r["decision_id"] for r in resolved.values())
    for ref, row in resolved.items():
        assert row["verdict"] == "follow" and row["resolves"] == asks[ref]["decision_id"]
        assert row["idempotency_key"] == f"resolve:{asks[ref]['decision_id']}:{out['precedent']['id']}"
        assert row["caller"] == "precedenz"
    # Re-assessment reuses the stored facts: the follow-up requests carry none.
    followups = [req for req, resolves in fake_service.calls if resolves]
    assert followups and all(req.facts is None and req.text is None for req in followups)

    answer_row = conn.execute(
        "SELECT * FROM harness_answers WHERE answer_id = ?", (out["answer_id"],)
    ).fetchone()
    assert answer_row["remembered"] and answer_row["precedent_id"] == out["precedent"]["id"]

    kinds = [e["type"] for e in events.since(seq)]
    assert kinds.index("case.answered") < kinds.index("precedent.decided")
    decided = next(e for e in events.since(seq) if e["type"] == "precedent.decided")
    assert decided == {**decided, "workstream": MEAL, "version": 1, "decided_by": DANA,
                       "origin_case": "EXP-1031", "text": EXPECTED_TEXT}

    # The asks the precedent does not cover keep waiting, and the asks themselves never change.
    waiting = {r["case_ref"] for r in service.open_asks(conn, MEAL)}
    assert waiting == {"EXP-1034", "EXP-1035", "EXP-1037"}
    original = conn.execute(
        "SELECT verdict, resolves FROM harness_decisions WHERE decision_id = ?",
        (asks["EXP-1032"]["decision_id"],),
    ).fetchone()
    assert original == {"verdict": "ask", "resolves": None}

    # A second precedent supersedes the first: one active per workstream.
    second_ask = send_incoming(fake_service, MEAL, "EXP-1040")
    assert second_ask["verdict"] == "follow"


def test_overrule_stops_the_precedent_and_the_next_case_asks(conn, fake_service):
    plant_meals()
    ask = send_incoming(fake_service, MEAL, "EXP-1031")
    precedent = remember_demo(ask["decision_id"])["precedent"]
    assert send_incoming(fake_service, MEAL, "EXP-1038")["verdict"] == "follow"
    seq = events.last_seq()

    out = service.overrule(precedent["id"], reason="audit flagged weekend claims", actor=DANA)
    assert out["status"] == "retired" and out["workstream"] == MEAL

    after = send_incoming(fake_service, MEAL, "EXP-1041")
    assert after["verdict"] == "ask" and after["reasons"] == [NO_PRECEDENT]

    overruled = [e for e in events.since(seq) if e["type"] == "precedent.overruled"]
    assert overruled[-1] == {**overruled[-1], "precedent_id": precedent["id"], "workstream": MEAL,
                             "by": DANA, "reason": "audit flagged weekend claims"}
    entry = conn.execute(
        "SELECT payload FROM audit_log WHERE policy_id = ? AND event_type = 'policy.retired'",
        (precedent["id"],),
    ).fetchone()
    assert json.loads(entry["payload"])["reason"] == "audit flagged weekend claims"

    with pytest.raises(service.Conflict):
        service.overrule(precedent["id"], reason="again", actor=DANA)
    with pytest.raises(service.NotFound):
        service.overrule("pol_missing", reason="x", actor=DANA)
    with pytest.raises(service.Invalid):
        service.overrule(precedent["id"], reason="  ", actor=DANA)


def test_a_bare_number_widens_the_one_amount_field(fake_service):
    plant_meals()
    ask = send_incoming(fake_service, MEAL, "EXP-1031")
    out = service.answer(ask["decision_id"], "approve", remember=True, up_to=75,
                         ignore=["client_meeting", "attendees"], actor=DANA, approval_token=APPROVAL)
    assert out["precedent"]["text"] == EXPECTED_TEXT


def test_refusals_are_the_builtin_exceptions_generic_callers_map():
    assert issubclass(service.NotFound, LookupError)
    assert issubclass(service.Forbidden, PermissionError)
    assert issubclass(service.Invalid, ValueError) and issubclass(service.Conflict, ValueError)
    assert (service.NotFound.status_code, service.Forbidden.status_code,
            service.Invalid.status_code, service.Conflict.status_code) == (404, 403, 422, 409)


def test_built_in_answers_are_recorded_but_not_remembered(conn):
    conn.execute(
        """INSERT INTO harness_decisions (decision_id, workstream, case_ref, idempotency_key,
               input_hash, verdict, caller, trace_id, created_at)
           VALUES ('dec_builtin', 'returns.missing_accessory', 'CASE-2003', 'k1', 'h', 'ask',
                   'api', 'trc_x', '2026-10-25T00:00:00Z')"""
    )
    out = service.answer("dec_builtin", "replace the missing part", actor=DANA, approval_token=APPROVAL)
    assert out["precedent"] is None
    with pytest.raises(service.Conflict, match="teach built-in workstreams in the inbox"):
        service.answer("dec_builtin", "replace the missing part", remember=True,
                       actor=DANA, approval_token=APPROVAL)
