"""The map's snapshot, its live event stream, and its person-only actions."""

from __future__ import annotations

import asyncio
import json
import sys
import types

import pytest
from fastapi.testclient import TestClient

from tests.conftest import demo_policy_payload

DANA = "dana.r@wharf-canary-optics.example"
RETURNS = "returns.missing_accessory"


@pytest.fixture()
def client(temp_db):
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def bus():
    from app.harness import events

    events.clear()
    yield events
    events.clear()


@pytest.fixture()
def tables_only(monkeypatch):
    """Read receipts from the tables, as if the decision service were not merged."""
    from app.harness import map_data

    real = map_data._optional
    monkeypatch.setattr(
        map_data, "_optional",
        lambda module, name: None if module == "app.harness.service" else real(module, name),
    )


@pytest.fixture()
def no_mirror(monkeypatch):
    """No Mubit mirror: its task keys live in process memory, shared across tests."""
    from app.harness import map_data

    real = map_data._optional
    monkeypatch.setattr(
        map_data, "_optional",
        lambda module, name: None if module == "app.mubit" else real(module, name),
    )


@pytest.fixture()
def workstream_service(monkeypatch):
    """Builder C's service with fakes for `answer` and `overrule`, merged or not."""
    calls: dict[str, list] = {"answer": [], "overrule": []}
    behaviour: dict[str, Exception | None] = {"raise": None}

    def answer(decision_id, answer, **kwargs):
        calls["answer"].append((decision_id, answer, kwargs))
        if behaviour["raise"]:
            raise behaviour["raise"]
        return {"answer_id": "ans_1", "precedent": {"id": "pol_new", "version": 1},
                "replay": {"passed": True, "cases_replayed": 12}, "resolved": []}

    def overrule(precedent_id, **kwargs):
        calls["overrule"].append((precedent_id, kwargs))
        if behaviour["raise"]:
            raise behaviour["raise"]
        return {"precedent_id": precedent_id, "status": "retired"}

    try:
        from app.workstreams import service
    except ImportError:
        import app.workstreams as package

        service = types.ModuleType("app.workstreams.service")
        monkeypatch.setitem(sys.modules, "app.workstreams.service", service)
        monkeypatch.setattr(package, "service", service, raising=False)
    monkeypatch.setattr(service, "answer", answer, raising=False)
    monkeypatch.setattr(service, "overrule", overrule, raising=False)
    calls["behaviour"] = behaviour
    return calls


# ------------------------------------------------------------------ seeding


def _policy(conn, policy_id, version, status, *, activated_by=DANA, activated=True, family=RETURNS):
    definition = demo_policy_payload(
        family=family, description=f"Replace one cheap accessory (v{version})."
    )
    conn.execute(
        """INSERT INTO policies (policy_id, family, version, status, definition, origin_case_id,
                                 created_at, activated_at, activated_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (policy_id, family, version, status, json.dumps(definition), "CASE-2001",
         f"2026-10-25T01:0{version}:00Z", f"2026-10-25T01:0{version}:30Z" if activated else None,
         activated_by if activated else None),
    )


def _decision(conn, decision_id, verdict, at, *, case_ref, precedent_id=None, resolves=None,
              caller="api", calls=0, task_key=None, reasons=()):
    conn.execute(
        """INSERT INTO harness_decisions (decision_id, workstream, case_ref, idempotency_key,
               input_hash, verdict, precedent_id, precedent_version, reasons, model_calls,
               task_key, caller, trace_id, resolves, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (decision_id, RETURNS, case_ref, f"key-{decision_id}", "sha256:x", verdict, precedent_id,
         1 if precedent_id else None, json.dumps(list(reasons)),
         json.dumps([{"via": "mubit", "request_id": f"mreq_{i}"} for i in range(calls)]),
         task_key, caller, f"trace-{decision_id}", resolves, at),
    )


def _exception(conn, exception_id, case_id, status, at, *, policy_id=None, reasons=()):
    conn.execute(
        """INSERT INTO exceptions (exception_id, case_id, family, trace_id, status, event_key,
               summary, escalation_reasons, applied_policy_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (exception_id, case_id, RETURNS, f"trace-{exception_id}", status, f"evt::{exception_id}",
         status, json.dumps(list(reasons)), policy_id, at, at),
    )


@pytest.fixture()
def seeded(temp_db):
    from app import db

    with db.session() as conn:
        _policy(conn, "pol_old", 1, "retired")
        _policy(conn, "pol_live", 2, "active")
        _policy(conn, "pol_draft", 3, "candidate", activated=False)
        _decision(conn, "dec_f1", "follow", "2026-10-25T02:00:00Z", case_ref="CASE-2002",
                  precedent_id="pol_live", caller="agent", calls=2, task_key="k1")
        _decision(conn, "dec_a1", "ask", "2026-10-25T02:01:00Z", case_ref="CASE-2003",
                  reasons=["serial does not match"], task_key="k2")
        _decision(conn, "dec_a2", "ask", "2026-10-25T02:02:00Z", case_ref="CASE-2004",
                  reasons=["cost above the precedent"])
        _decision(conn, "dec_a3", "ask", "2026-10-25T02:03:00Z", case_ref="CASE-2005",
                  reasons=["no precedent yet"])
        _decision(conn, "dec_f2", "follow", "2026-10-25T02:04:00Z", case_ref="CASE-2005",
                  precedent_id="pol_live", resolves="dec_a3")
        conn.execute(
            """INSERT INTO harness_answers (answer_id, decision_id, answer, remembered,
                   precedent_id, actor, note, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("ans_1", "dec_a2", "escalate", False, None, "sam.k@wharf-canary-optics.example",
             "", "2026-10-25T02:05:00Z"),
        )
        conn.execute(
            """INSERT INTO harness_cases (workstream, case_ref, facts, text, source, received_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (RETURNS, "CASE-2003", json.dumps({"serial_match": False}), "serial label differs",
             "caller", "2026-10-25T02:01:00Z"),
        )
        _exception(conn, "exc_inbox1", "CASE-2001", "resolved", "2026-10-25T01:30:00Z",
                   policy_id="pol_live")
        _exception(conn, "exc_inbox2", "CASE-2006", "escalated", "2026-10-25T01:40:00Z",
                   reasons=["parts catalog unavailable"])


def _returns(snap):
    return next(ws for ws in snap["workstreams"] if ws["id"] == RETURNS)


# ---------------------------------------------------------------- snapshot


def test_snapshot_of_a_fresh_seed_is_complete_and_empty(temp_db, bus):
    from app.harness import map_data

    snap = map_data.snapshot()
    assert set(snap) >= {"generated_at", "last_seq", "people", "workstreams", "templates", "totals"}
    assert snap["last_seq"] == 0
    assert snap["totals"] == {"answered_once": 0, "repeated": 0, "asked": 0, "model_calls": 0}
    assert snap["people"] == [{"id": DANA, "label": "Dana R."}]
    ids = {ws["id"] for ws in snap["workstreams"]}
    assert {RETURNS, "ap.invoice_variance"} <= ids
    returns = _returns(snap)
    assert returns["kind"] == "built-in"
    assert returns["precedents"] == [] and returns["waiting"] == []
    assert returns["counts"] == {"follow": 0, "ask": 0, "answered": 0, "model_calls": 0}
    assert returns["repeatable_answer"]
    assert {f["kind"] for f in returns["fields"]} <= {"flag", "category", "amount", "count"}
    assert returns["mubit"] is None or isinstance(returns["mubit"], dict)


def test_snapshot_never_raises_when_a_table_is_gone(temp_db):
    from app import db
    from app.harness import map_data

    with db.session() as conn:
        conn.execute("DROP TABLE harness_answers")
        conn.execute("DROP TABLE harness_cases")
    snap = map_data.snapshot()
    assert snap["workstreams"] and snap["people"]


def test_snapshot_groups_follows_under_precedents_and_lists_waiting(seeded, bus, no_mirror):
    from app.harness import map_data

    bus.publish("decision.started", workstream=RETURNS, case_ref="CASE-2009", caller="test")
    snap = map_data.snapshot()
    assert snap["last_seq"] == 1
    returns = _returns(snap)

    precedents = {p["id"]: p for p in returns["precedents"]}
    assert set(precedents) == {"pol_old", "pol_live"}  # a candidate is not a precedent
    assert precedents["pol_old"]["status"] == "retired"
    live = precedents["pol_live"]
    assert live["status"] == "active"
    assert live["text"] == "Replace one cheap accessory (v2)."
    assert live["decided_by"] == DANA and live["origin_case"] == "CASE-2001"
    cases = {c["decision_id"]: c for c in live["cases"]}
    assert set(cases) == {"dec_f1", "dec_f2", "exc_inbox1"}
    assert cases["dec_f1"]["via"] == "api" and cases["dec_f1"]["caller"] == "agent"
    assert cases["dec_f2"]["resolves"] == "dec_a3"
    assert cases["exc_inbox1"]["via"] == "inbox" and cases["exc_inbox1"]["case_ref"] == "CASE-2001"

    waiting = {w["decision_id"]: w for w in returns["waiting"]}
    # dec_a2 was answered and dec_a3 was resolved by a later follow: neither waits.
    assert set(waiting) == {"dec_a1", "exc_inbox2"}
    assert waiting["dec_a1"]["reasons"] == ["serial does not match"]
    assert waiting["dec_a1"]["facts"] == {"serial_match": False}
    assert waiting["dec_a1"]["text"] == "serial label differs"
    assert waiting["dec_a1"]["answerable"] is True
    assert waiting["exc_inbox2"]["via"] == "inbox" and waiting["exc_inbox2"]["answerable"] is False

    assert returns["counts"] == {"follow": 3, "ask": 4, "answered": 1, "model_calls": 2}
    assert returns["answered"][0]["decision_id"] == "dec_a2"
    assert returns["task_key"] == "k2" and returns["task_key_changed"] is True
    assert snap["totals"] == {"answered_once": 2, "repeated": 3, "asked": 4, "model_calls": 2}
    assert snap["people"][0] == {"id": DANA, "label": "Dana R."}
    assert {"id": "sam.k@wharf-canary-optics.example", "label": "Sam K."} in snap["people"]


def test_snapshot_shows_the_mubit_mirror_and_survives_its_failure(seeded, monkeypatch):
    from app.harness import map_data

    mirror = types.ModuleType("app.mubit")
    mirror.console_stage = lambda family: (
        {"stage": "Collecting examples", "detail": "18 of 25 examples",
         "source": "Mubit console", "as_of": "2026-10-25T03:00:00Z"} if family == RETURNS else None
    )
    mirror.task_key_info = lambda family: {"value": "7f3a9c", "changed": True, "since": None,
                                           "previous": "1b2c3d"}
    monkeypatch.setitem(sys.modules, "app.mubit", mirror)
    returns = _returns(map_data.snapshot())
    assert returns["mubit"]["stage"] == "Collecting examples"
    assert returns["task_key"] == "7f3a9c" and returns["task_key_changed"] is True
    assert returns["task_key_previous"] == "1b2c3d"

    def broken(family):
        raise RuntimeError("snapshot unreadable")

    mirror.console_stage = broken
    mirror.task_key_info = broken
    returns = _returns(map_data.snapshot())
    assert returns["mubit"] is None
    assert returns["task_key"] == "k2"  # falls back to the receipts' own task keys


def test_snapshot_uses_the_workstream_service_and_keeps_the_built_ins(temp_db, monkeypatch):
    from app.harness import map_data

    planted = {
        "id": "expenses.meal_claim", "label": "Meal expense claims", "kind": "planted",
        "audience": "team", "question": "Should this meal claim be approved?", "subject": "meal claim",
        "repeatable_answer": "approve", "other_answers": ["reject"], "reader": True, "evidence": "text",
        "fields": [{"name": "amount_usd", "kind": "amount", "label": "Amount", "ceiling": 500.0}],
    }
    service = types.ModuleType("app.workstreams.service")
    service.list_workstreams = lambda: [planted]
    service.list_templates = lambda: [{"id": "calendar.meeting_invite",
                                       "contract": {"label": "Meeting invites", "audience": "individual"}}]
    real = map_data._optional
    monkeypatch.setattr(map_data, "_optional", lambda module, name: (
        getattr(service, name, None) if module == "app.workstreams.service" else real(module, name)))

    snap = map_data.snapshot()
    by_id = {ws["id"]: ws for ws in snap["workstreams"]}
    assert {RETURNS, "ap.invoice_variance", "expenses.meal_claim"} <= set(by_id)
    meals = by_id["expenses.meal_claim"]
    assert meals["kind"] == "planted" and meals["repeatable_answer"] == "approve"
    assert meals["other_answers"] == ["reject"]
    assert meals["fields"][0] == {"name": "amount_usd", "kind": "amount", "label": "Amount", "ceiling": 500.0}
    assert by_id[RETURNS]["kind"] == "built-in"
    assert snap["templates"] == [{"id": "calendar.meeting_invite", "label": "Meeting invites",
                                  "audience": "individual", "planted": False}]


def test_person_labels():
    from app.harness.map_data import person_label

    assert person_label(DANA) == "Dana R."
    assert person_label("priya@example.com") == "Priya"
    assert person_label("agent") == "agent"


# ------------------------------------------------------------------ routes


def test_map_data_route(client, seeded):
    r = client.get("/map/data")
    assert r.status_code == 200
    assert any(ws["id"] == RETURNS and ws["precedents"] for ws in r.json()["workstreams"])


def test_receipt_route(client, seeded, tables_only):
    r = client.get("/map/receipt/dec_a2")
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "ask" and body["reasons"] == ["cost above the precedent"]
    assert body["answers"][0]["answer"] == "escalate"
    assert client.get("/map/receipt/dec_a3").json()["resolved_by"] == "dec_f2"
    assert client.get("/map/receipt/dec_f1").json()["precedent"]["id"] == "pol_live"

    inbox = client.get("/map/receipt/exc_inbox2").json()
    assert inbox["via"] == "inbox" and inbox["link"] == "/exceptions/exc_inbox2"

    missing = client.get("/map/receipt/dec_nope")
    assert missing.status_code == 404 and "dec_nope" in missing.json()["detail"]


def test_answer_needs_the_approval_token(client, workstream_service):
    body = {"answer": "approve", "remember": True}
    assert client.post("/map/answer/dec_1", json=body).status_code == 403
    assert client.post("/map/answer/dec_1", json=body | {"approval_token": "nope"}).status_code == 403
    assert workstream_service["answer"] == []


def test_answer_calls_the_workstream_service_as_the_operator(client, workstream_service, approval_token):
    r = client.post("/map/answer/dec_1", json={
        "answer": "approve", "remember": True, "up_to": {"amount_usd": 75},
        "ignore": ["client_meeting"], "note": "fine", "approval_token": approval_token,
    })
    assert r.status_code == 200, r.text
    assert r.json()["precedent"]["id"] == "pol_new"
    decision_id, answer, kwargs = workstream_service["answer"][0]
    assert (decision_id, answer) == ("dec_1", "approve")
    assert kwargs == {"remember": True, "up_to": {"amount_usd": 75}, "ignore": ["client_meeting"],
                      "note": "fine", "actor": DANA, "approval_token": approval_token}


def test_answer_and_overrule_map_refusals(client, workstream_service, approval_token):
    behaviour = workstream_service["behaviour"]
    behaviour["raise"] = KeyError("no decision 'dec_x'")
    r = client.post("/map/answer/dec_x", json={"answer": "approve", "approval_token": approval_token})
    assert r.status_code == 404 and r.json() == {"detail": "no decision 'dec_x'"}

    behaviour["raise"] = ValueError("that question was already answered")
    r = client.post("/map/answer/dec_x", json={"answer": "approve", "approval_token": approval_token})
    assert r.status_code == 409 and r.json() == {"detail": "that question was already answered"}

    behaviour["raise"] = None
    assert client.post("/map/overrule/pol_1", json={"reason": "wrong"}).status_code == 403
    r = client.post("/map/overrule/pol_1", json={"reason": "wrong", "approval_token": approval_token})
    assert r.status_code == 200
    assert workstream_service["overrule"] == [("pol_1", {"reason": "wrong", "actor": DANA})]


# --------------------------------------------------------------------- SSE


def test_stream_start_honors_last_event_id_then_since():
    from app.api.map import start_seq

    assert start_seq("7", 3) == 7
    assert start_seq(None, 3) == 3
    assert start_seq("garbage", 2) == 2
    assert start_seq(None, None) is None


def test_stream_yields_an_event_published_after_connect(bus):
    from app.api.map import event_stream

    async def run():
        stream = event_stream(None, heartbeat=5)
        assert (await stream.__anext__()).startswith("retry:")
        pending = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0.1)
        assert not pending.done()
        bus.publish("decision.finished", decision_id="dec_9", workstream=RETURNS,
                    case_ref="CASE-2002", verdict="follow", caller="test")
        frame = await asyncio.wait_for(pending, timeout=5)
        await stream.aclose()
        return frame

    frame = asyncio.run(run())
    lines = frame.strip().splitlines()
    assert lines[0] == "id: 1"
    event = json.loads(lines[1].removeprefix("data: "))
    assert event["type"] == "decision.finished" and event["decision_id"] == "dec_9"


def test_stream_replays_after_last_event_id_and_sends_heartbeats(bus):
    from app.api.map import event_stream

    for n in range(3):
        bus.publish("model.call", workstream=RETURNS, case_ref=f"CASE-{n}", ms=n)

    async def run():
        stream = event_stream(1, heartbeat=0.05)
        frames = [await stream.__anext__() for _ in range(4)]
        await stream.aclose()
        return frames

    preamble, second, third, heartbeat = asyncio.run(run())
    assert preamble.startswith("retry:")
    assert second.startswith("id: 2\n") and third.startswith("id: 3\n")
    assert heartbeat == ": heartbeat\n\n"


def test_stream_after_a_restart_starts_over(bus):
    from app.api.map import event_stream

    bus.publish("workstream.planted", workstream="expenses.meal_claim", label="Meals", audience="team")

    async def run():
        stream = event_stream(500, heartbeat=1)  # the client remembers a bus from before
        frames = [await stream.__anext__() for _ in range(2)]
        await stream.aclose()
        return frames

    _, frame = asyncio.run(run())
    assert frame.startswith("id: 1\n")


def test_stream_follows_the_bus_when_it_is_cleared(bus):
    from app.api.map import event_stream

    for n in range(3):
        bus.publish("model.call", workstream=RETURNS, case_ref=f"CASE-{n}", ms=n)

    async def run():
        stream = event_stream(3, heartbeat=0.05)
        await stream.__anext__()  # preamble
        bus.clear()
        assert await stream.__anext__() == ": heartbeat\n\n"
        bus.publish("decision.started", workstream=RETURNS, case_ref="CASE-9", caller="test")
        frame = await asyncio.wait_for(stream.__anext__(), timeout=5)
        await stream.aclose()
        return frame

    assert asyncio.run(run()).startswith("id: 1\n")
