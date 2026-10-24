"""The /v1 decision routes: what a pipeline, the CLI and the MCP server call.

Status codes are the contract (docs/decide-once-plan.md, 5.1): 401 for a bad
token, 404 for an unknown workstream or decision, 409 for an idempotency
conflict, 422 for a malformed body. A model, Sapling or tool failure is never
an error; it is an ask.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, orchestrator
from tests.test_harness_service import remember
from tests.conftest import demo_policy_payload

RETURNS = "returns.missing_accessory"


@pytest.fixture()
def client(temp_db):
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {config.get_settings().api_token}"}


@pytest.fixture()
def precedent(conn, approval_token) -> str:
    return remember(conn, approval_token, demo_policy_payload(), "CASE-2001")


def body(case_ref: str = "CASE-2002", key: str | None = None, **extra) -> dict:
    return {"workstream": RETURNS, "case_ref": case_ref,
            "idempotency_key": key or f"api::{case_ref}", **extra}


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "wrong"}, {"Authorization": "Bearer "}],
)
def test_every_route_needs_the_api_token(client, headers):
    assert client.post("/v1/decide", json=body(), headers=headers).status_code == 401
    assert client.get("/v1/decisions", headers=headers).status_code == 401
    assert client.get("/v1/decisions/dec_x", headers=headers).status_code == 401
    assert client.post(
        "/v1/decisions/dec_x/outcome", json={"status": "executed"}, headers=headers
    ).status_code == 401


def test_the_approval_token_is_not_an_api_token(client, approval_token):
    response = client.post(
        "/v1/decide", json=body(), headers={"Authorization": f"Bearer {approval_token}"}
    )
    assert response.status_code == 401


def test_decide_follows_a_precedent(client, auth, precedent):
    response = client.post(
        "/v1/decide", json=body(), headers={**auth, "X-Precedenz-Caller": "agent"}
    )
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["verdict"] == "follow"
    assert receipt["precedent"]["id"] == precedent
    assert receipt["caller"] == "agent"
    assert receipt["serving_identity"] == "not reported by the gateway"


def test_decide_asks_with_reasons(client, auth, precedent):
    receipt = client.post("/v1/decide", json=body("CASE-2003"), headers=auth).json()
    assert receipt["verdict"] == "ask"
    assert receipt["actions"] == []
    assert any("serial number does not match" in r for r in receipt["reasons"])


def test_a_model_failure_is_an_ask_not_an_error(client, auth, precedent, monkeypatch):
    def broken(*a, **k):
        raise TimeoutError("gateway timed out")

    monkeypatch.setattr(orchestrator, "assess_case", broken)
    response = client.post("/v1/decide", json=body(), headers=auth)
    assert response.status_code == 200
    assert response.json()["reasons"] == ["TimeoutError: gateway timed out"]


def test_an_unknown_workstream_is_404(client, auth):
    response = client.post(
        "/v1/decide", json={**body(), "workstream": "nobody.planted_this"}, headers=auth
    )
    assert response.status_code == 404
    assert "nobody.planted_this" in response.json()["detail"]


def test_a_reused_key_is_idempotent_and_a_changed_input_is_409(client, auth, precedent):
    first = client.post("/v1/decide", json=body(key="evt-1", source_revision="r3"), headers=auth)
    again = client.post("/v1/decide", json=body(key="evt-1", source_revision="r3"), headers=auth)
    changed = client.post("/v1/decide", json=body(key="evt-1", source_revision="r4"), headers=auth)

    assert first.status_code == again.status_code == 200
    assert again.json() == first.json()
    assert changed.status_code == 409
    assert first.json()["decision_id"] in changed.json()["detail"]


@pytest.mark.parametrize(
    "payload",
    [
        {"workstream": RETURNS, "idempotency_key": "k"},  # no case_ref
        {"workstream": RETURNS, "case_ref": "CASE-2002"},  # no idempotency key
        {"workstream": RETURNS, "case_ref": "", "idempotency_key": "k"},
        {"workstream": RETURNS, "case_ref": "CASE-2002", "idempotency_key": "k", "facts": [1, 2]},
    ],
)
def test_a_malformed_body_is_422(client, auth, payload):
    assert client.post("/v1/decide", json=payload, headers=auth).status_code == 422


def test_a_receipt_is_readable_by_id(client, auth, precedent):
    receipt = client.post("/v1/decide", json=body(), headers=auth).json()
    read = client.get(f"/v1/decisions/{receipt['decision_id']}", headers=auth)

    assert read.status_code == 200
    got = read.json()
    assert {k: got[k] for k in receipt} == receipt
    assert got["answers"] == [] and got["outcomes"] == [] and got["resolved_by"] is None
    assert client.get("/v1/decisions/dec_nope", headers=auth).status_code == 404


def test_open_questions_list_the_waiting_asks(client, auth, precedent):
    ask = client.post("/v1/decide", json=body("CASE-2003"), headers=auth).json()
    client.post("/v1/decide", json=body("CASE-2002"), headers=auth)

    listed = client.get("/v1/decisions?verdict=ask&open=true", headers=auth).json()
    assert [d["decision_id"] for d in listed["decisions"]] == [ask["decision_id"]]
    assert listed["count"] == 1
    scoped = client.get(
        "/v1/decisions", params={"verdict": "ask", "open": "true", "workstream": "ap.invoice_variance"},
        headers=auth,
    ).json()
    assert scoped["decisions"] == []
    everything = client.get("/v1/decisions", headers=auth).json()
    assert everything["count"] == 2
    follows = client.get("/v1/decisions?verdict=follow", headers=auth).json()
    assert [d["case_ref"] for d in follows["decisions"]] == ["CASE-2002"]


def test_an_outcome_is_reported_and_appended(client, auth, precedent):
    receipt = client.post("/v1/decide", json=body(), headers=auth).json()
    url = f"/v1/decisions/{receipt['decision_id']}/outcome"

    response = client.post(
        url, json={"status": "executed", "detail": "WO-1 raised"},
        headers={**auth, "X-Precedenz-Caller": "script"},
    )
    assert response.status_code == 200, response.text
    outcome = response.json()
    assert outcome["status"] == "executed" and outcome["verification"] == "reported"
    assert outcome["actor"] == "script"

    read = client.get(f"/v1/decisions/{receipt['decision_id']}", headers=auth).json()
    assert read["outcomes"] == [outcome]
    assert client.post(url, json={"status": "approved"}, headers=auth).status_code == 422
    assert client.post(
        "/v1/decisions/dec_nope/outcome", json={"status": "failed"}, headers=auth
    ).status_code == 404
