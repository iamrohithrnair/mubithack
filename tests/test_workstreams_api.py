"""The workstream routes, end to end over HTTP with the real decision service.

plant -> five claims ask -> answer one and remember it -> the waiting claims it
covers resolve -> new claims follow -> overrule -> the next claim asks.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config
from tests.test_workstreams_support import APPROVAL, incoming, isolated_registry  # noqa: F401

pytestmark = pytest.mark.usefixtures("isolated_registry")

MEAL = "expenses.meal_claim"


@pytest.fixture()
def client(temp_db):
    from app.main import app

    with TestClient(app) as c:
        yield c


def caller() -> dict[str, str]:
    return {"Authorization": f"Bearer {config.get_settings().api_token}"}


def person() -> dict[str, str]:
    return {**caller(), "X-Approval-Token": APPROVAL}


def decide(client: TestClient, case_ref: str, **overrides) -> dict:
    item = incoming(MEAL, case_ref)
    body = {
        "workstream": MEAL,
        "case_ref": case_ref,
        "idempotency_key": f"evt-{case_ref}",
        "facts": {**item["facts"], **overrides},
        "text": item["text"],
    }
    response = client.post("/v1/decide", json=body, headers=caller())
    assert response.status_code == 200, response.text
    return response.json()


def test_reading_routes_need_the_api_token(client):
    assert client.get("/v1/templates").status_code == 401
    assert client.get("/v1/workstreams").status_code == 401
    templates = client.get("/v1/templates", headers=caller()).json()["templates"]
    assert {t["id"] for t in templates} == {MEAL, "calendar.meeting_invite", "it.access_request"}
    kinds = {w["id"]: w["kind"] for w in client.get("/v1/workstreams", headers=caller()).json()["workstreams"]}
    assert kinds == {"returns.missing_accessory": "built-in", "ap.invoice_variance": "built-in"}


def test_planting_needs_a_person_and_a_valid_contract(client):
    assert client.post("/v1/workstreams", json={"template": MEAL}, headers=caller()).status_code == 403
    assert client.post("/v1/workstreams", json={"template": MEAL},
                       headers={**caller(), "X-Approval-Token": "wrong"}).status_code == 403
    assert client.post("/v1/workstreams", json={"template": "nope.nothing"}, headers=person()).status_code == 404

    bad = {"id": "expenses.bad", "label": "Bad", "subject": "claim", "question": "Approve it?",
           "owner": "me", "fields": [{"name": "kind_of", "kind": "category", "label": "Kind"}],
           "repeatable_answer": "approve"}
    response = client.post("/v1/workstreams", json={"contract": bad}, headers=person())
    assert response.status_code == 422
    assert any("must list its values" in e for e in response.json()["detail"]["errors"])

    response = client.post("/v1/workstreams", json={"template": MEAL}, headers=person())
    assert response.status_code == 201, response.text
    assert response.json()["history_imported"] == 12
    assert response.json()["planted_by"] == "dana.r@wharf-canary-optics.example"
    listed = {w["id"] for w in client.get("/v1/workstreams", headers=caller()).json()["workstreams"]}
    assert MEAL in listed


def test_decide_once_end_to_end(client):
    assert client.post("/v1/workstreams", json={"template": MEAL}, headers=person()).status_code == 201

    waiting = {ref: decide(client, ref) for ref in ("EXP-1031", "EXP-1032", "EXP-1033", "EXP-1034", "EXP-1036")}
    assert {r["verdict"] for r in waiting.values()} == {"ask"}
    assert waiting["EXP-1031"]["reasons"] == ["no precedent yet: a person decides once"]
    assert waiting["EXP-1034"]["reasons"] == ["always ask: alcohol_only is true"]

    ask_id = waiting["EXP-1031"]["decision_id"]
    body = {"answer": "approve", "remember": True, "up_to": {"amount_usd": 75},
            "ignore": ["client_meeting", "attendees"], "note": "client meeting doesn't matter"}
    assert client.post(f"/v1/decisions/{ask_id}/answer", json=body, headers=caller()).status_code == 403
    assert client.post("/v1/decisions/dec_missing/answer", json=body, headers=person()).status_code == 404
    wrong = client.post(f"/v1/decisions/{ask_id}/answer", json={**body, "answer": "decline"}, headers=person())
    assert wrong.status_code == 422
    contradiction = client.post(
        f"/v1/decisions/{ask_id}/answer", json={**body, "up_to": {"amount_usd": 100}}, headers=person()
    )
    assert contradiction.status_code == 409
    assert any("EXP-0907" in e for e in contradiction.json()["detail"]["errors"])

    response = client.post(f"/v1/decisions/{ask_id}/answer", json=body, headers=person())
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["precedent"]["text"] == (
        "Approve meal claims up to $75.00 when a receipt is attached and it is not alcohol only."
    )
    assert out["replay"] == {"cases_replayed": 13, "false_automatic_actions": 0, "passed": True}
    assert len(out["resolved"]) == 3  # EXP-1032, EXP-1033, EXP-1036; the alcohol-only claim keeps waiting

    receipts = [client.get(f"/v1/decisions/{d}", headers=caller()).json() for d in out["resolved"]]
    assert {r["case_ref"] for r in receipts} == {"EXP-1032", "EXP-1033", "EXP-1036"}
    for receipt in receipts:
        assert receipt["verdict"] == "follow"
        assert receipt["resolves"] == waiting[receipt["case_ref"]]["decision_id"]
        assert receipt["precedent"]["id"] == out["precedent"]["id"]
        assert receipt["actions"] == [{"type": "answer", "answer": "approve", "max_amount_usd": 75.0}]

    open_now = client.get("/v1/decisions?verdict=ask&open=true", headers=caller()).json()
    open_refs = {d["case_ref"] for d in open_now["decisions"]}
    assert "EXP-1034" in open_refs and "EXP-1032" not in open_refs and "EXP-1031" not in open_refs

    follow = decide(client, "EXP-1041")
    assert follow["verdict"] == "follow" and follow["precedent"]["origin_case"] == "EXP-1031"
    near = decide(client, "EXP-1035")
    assert near["verdict"] == "ask" and near["reasons"] == ["amount_usd 140.00 is above the precedent's 75.00"]

    precedent_id = out["precedent"]["id"]
    overrule = {"reason": "finance wants to see client dinners again"}
    assert client.post(f"/v1/precedents/{precedent_id}/overrule", json=overrule, headers=caller()).status_code == 403
    response = client.post(f"/v1/precedents/{precedent_id}/overrule", json=overrule, headers=person())
    assert response.status_code == 200 and response.json()["status"] == "retired"
    assert client.post(f"/v1/precedents/{precedent_id}/overrule", json=overrule, headers=person()).status_code == 409
    assert client.post("/v1/precedents/pol_missing/overrule", json=overrule, headers=person()).status_code == 404

    after = decide(client, "EXP-1044")
    assert after["verdict"] == "ask" and after["reasons"] == ["no precedent yet: a person decides once"]


def test_remember_on_a_built_in_workstream_is_a_conflict(client):
    response = client.post(
        "/v1/decide",
        json={"workstream": "ap.invoice_variance", "case_ref": "AP-2002", "idempotency_key": "evt-inv"},
        headers=caller(),
    )
    assert response.status_code == 200, response.text
    receipt = response.json()
    if receipt["verdict"] != "ask":
        pytest.skip("this invoice did not ask")
    body = {"answer": "approve the variance", "remember": True}
    response = client.post(f"/v1/decisions/{receipt['decision_id']}/answer", json=body, headers=person())
    assert response.status_code == 409
    assert "teach built-in workstreams in the inbox" in response.json()["detail"]["error"]
    response = client.post(
        f"/v1/decisions/{receipt['decision_id']}/answer", json={**body, "remember": False}, headers=person()
    )
    assert response.status_code == 200 and response.json()["precedent"] is None
