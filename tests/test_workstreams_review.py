"""Holes an independent review found in the decide-once loop, each pinned by a test.

1. Replay ground truth cannot be rewritten by a later arrival under a past case_ref.
2. Remember drafts from the decision being answered, never the case's latest facts.
3. An ambiguous amount ("1.000,50") asks instead of parsing to 1.00.
6. An agent is never told how to answer, and the CLI never answers with a default token.
7. An optional field unknown on the answered case must be ignored explicitly.
Plus the demo flow, end to end through the real decision service.
"""

from __future__ import annotations

import io
import json
import sys

import pytest

from app import domains
from app.harness.engines import DecideRequest
from app.workstreams import cases, registry, service
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
DEMO = {"up_to": {"amount_usd": 75}, "ignore": ["client_meeting", "attendees"]}
EXPECTED_TEXT = "Approve meal claims up to $75.00 when a receipt is attached and it is not alcohol only."


def remember(decision_id: str, **options) -> dict:
    return service.answer(decision_id, "approve", remember=True, actor=DANA, approval_token=APPROVAL,
                          **{**DEMO, **options})


def rewrite_case(conn, case_ref: str, **changes) -> None:
    """Change the case on file without any new decision, as a failed receipt write would."""
    stored = cases.load(conn, MEAL, case_ref)
    conn.execute(
        "UPDATE harness_cases SET facts = ? WHERE workstream = ? AND case_ref = ?",
        (json.dumps({**stored.facts, **changes}), MEAL, case_ref),
    )


# ------------------------------------------------------------------- 1


def test_replay_holds_to_each_past_answer_s_own_facts(conn, fake_service):
    service.plant(MEAL, actor=DANA)
    corpus_ids = [case_id for case_id, _, _ in domains.get(MEAL).corpus(conn)]
    assert len(corpus_ids) == 12 and all(c.startswith("answer:") for c in corpus_ids)

    # A caller reusing a past case's reference asks, and rewrites nothing: an
    # innocent $20 claim must not inherit EXP-0907's decline, nor overwrite it.
    for amount in (199, 20):
        reuse = send(fake_service, MEAL, "EXP-0907",
                     facts={**incoming(MEAL, "EXP-1031")["facts"], "amount_usd": amount}, text="lunch")
        assert reuse["verdict"] == "ask"
        assert reuse["reasons"] == [
            "EXP-0907 is one of this workstream's past answers; send a new meal claim under a case_ref of its own"
        ]
    assert cases.load(conn, MEAL, "EXP-0907").facts["amount_usd"] == 95.4

    # Even with the case row rewritten underneath, replay reads the answer's own
    # snapshot: EXP-0907 ($95.40) was declined, so "approve up to $100" is refused.
    rewrite_case(conn, "EXP-0907", amount_usd=199)
    ask = send_incoming(fake_service, MEAL, "EXP-1031")
    with pytest.raises(service.Conflict) as exc:
        remember(ask["decision_id"], up_to={"amount_usd": 100})
    assert any("EXP-0907" in e for e in exc.value.errors), exc.value.errors

    # Every answer keeps the facts it was given on.
    out = remember(ask["decision_id"])
    row = conn.execute("SELECT facts FROM harness_answers WHERE answer_id = ?", (out["answer_id"],)).fetchone()
    assert json.loads(row["facts"]) == incoming(MEAL, "EXP-1031")["facts"]


# ------------------------------------------------------------------- 2


def test_remember_drafts_from_the_answered_decision_and_refuses_a_changed_case(conn, fake_service):
    service.plant(MEAL, actor=DANA)
    ask = send_incoming(fake_service, MEAL, "EXP-1031", amount_usd=40)

    # The case on file changed after this decision (no new decision was made):
    # drafting from it would give "up to $90" for a $40 question, so it refuses.
    rewrite_case(conn, "EXP-1031", amount_usd=90)
    with pytest.raises(service.Conflict, match="changed after decision"):
        remember(ask["decision_id"], up_to={})
    assert conn.execute("SELECT COUNT(*) AS c FROM policies").fetchone()["c"] == 0

    rewrite_case(conn, "EXP-1031", amount_usd=40)
    out = remember(ask["decision_id"], up_to={})
    assert out["precedent"]["text"] == (
        "Approve meal claims up to $40.00 when a receipt is attached and it is not alcohol only."
    )


def test_a_superseded_ask_cannot_be_answered(conn, fake_service):
    service.plant(MEAL, actor=DANA)
    first = send_incoming(fake_service, MEAL, "EXP-1032", amount_usd=40)
    second = send_incoming(fake_service, MEAL, "EXP-1032", amount_usd=70)  # same case, a new key
    with pytest.raises(service.Conflict, match="superseded"):
        remember(first["decision_id"])
    with pytest.raises(service.Conflict, match="superseded"):
        service.answer(first["decision_id"], "decline", actor=DANA, approval_token=APPROVAL)
    out = remember(second["decision_id"], up_to={})
    assert out["precedent"]["text"].startswith("Approve meal claims up to $70.00")


def test_the_real_service_files_each_decision_s_facts(conn):
    from app.harness import service as harness_service

    service.plant(MEAL, actor=DANA)
    item = incoming(MEAL, "EXP-1033")
    receipt = harness_service.decide(
        DecideRequest(MEAL, "EXP-1033", "evt-facts", facts=item["facts"], text=item["text"])
    )
    row = conn.execute(
        "SELECT facts FROM harness_decisions WHERE decision_id = ?", (receipt["decision_id"],)
    ).fetchone()
    assert json.loads(row["facts"]) == item["facts"]


# ------------------------------------------------------------------- 3


@pytest.mark.parametrize(
    "raw, parsed",
    [
        ("62.40", 62.4), ("$62.40", 62.4), ("1,000.50", 1000.5), (" 140 ", 140.0), (75, 75.0),
        ("1.000,50", None), ("62,4", None), ("1,00", None), ("12abc", None), ("-5", None), ("", None),
    ],
)
def test_amounts_parse_only_unambiguous_text(raw, parsed):
    spec = registry.parse_contract(template(MEAL)["contract"]).field_named("amount_usd")
    if parsed is None:
        with pytest.raises(ValueError, match="amount_usd must be"):
            cases.coerce(spec, raw)
    else:
        assert cases.coerce(spec, raw) == parsed


def test_a_decimal_comma_asks_instead_of_slipping_under_the_ceiling(fake_service):
    service.plant(MEAL, actor=DANA)
    remember(send_incoming(fake_service, MEAL, "EXP-1031")["decision_id"])
    receipt = send_incoming(fake_service, MEAL, "EXP-1041", amount_usd="1.000,50")
    assert receipt["verdict"] == "ask"
    assert receipt["reasons"] == ["amount_usd must be a number, got '1.000,50'"]


# ------------------------------------------------------------------- 6


def test_an_agent_is_never_shown_how_to_answer():
    pytest.importorskip("mcp")
    from precedenz_client import mcp_server

    text = mcp_server._what_to_do({"verdict": "ask", "decision_id": "dec_1", "reasons": [NO_PRECEDENT]})
    assert "a PERSON must answer it" in text
    assert "precedenz answer" not in text and "`" not in text


def test_the_cli_answers_only_with_a_person_s_token(monkeypatch, capsys):
    from precedenz_client import cli

    monkeypatch.delenv("PRECEDENZ_APPROVAL_TOKEN", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))  # not a terminal: nobody to type it
    for argv in (["answer", "dec_1", "approve", "--remember"],
                 ["overrule", "pol_1", "--reason", "wrong"],
                 ["plant", MEAL]):
        assert cli.main(argv) == 2
        assert "set PRECEDENZ_APPROVAL_TOKEN" in capsys.readouterr().err

    class Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdin", Terminal(""))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "typed-by-dana")
    typed = cli.make_client(cli.build_parser().parse_args(["answer", "dec_1", "approve"]))
    assert typed.approval_token == "typed-by-dana"

    monkeypatch.setenv("PRECEDENZ_APPROVAL_TOKEN", "set-by-dana")
    assert cli.make_client(cli.build_parser().parse_args(["plant", MEAL])).approval_token == "set-by-dana"
    # Caller commands are unchanged: no approval token, and the API token default still applies.
    assert cli.make_client(cli.build_parser().parse_args(["open"])).approval_token is None


# ------------------------------------------------------------------- 7


def test_an_optional_field_unknown_on_the_case_must_be_ignored_explicitly(fake_service):
    contract = template(MEAL)["contract"]
    contract.update(id="expenses.team_lunch", label="Team lunches")
    for field in contract["fields"]:
        if field["name"] == "client_meeting":
            field["required"] = False
    service.plant(contract=contract, actor=DANA)

    facts = dict(incoming(MEAL, "EXP-1031")["facts"])
    del facts["client_meeting"]
    ask = send(fake_service, "expenses.team_lunch", "TL-1", facts=facts, text="team lunch")
    assert ask["reasons"] == [NO_PRECEDENT]

    with pytest.raises(service.Invalid, match="client_meeting is unknown on this case"):
        remember(ask["decision_id"], ignore=["attendees"])
    out = remember(ask["decision_id"], ignore=["attendees", "client_meeting"])
    assert out["precedent"]["text"] == EXPECTED_TEXT


# ---------------------------------------------------------- the demo flow


def test_the_demo_flow_through_the_real_decision_service(conn):
    from app.harness import service as harness_service

    def decide(case_ref: str) -> dict:
        item = incoming(MEAL, case_ref)
        return harness_service.decide(
            DecideRequest(MEAL, case_ref, f"evt-{case_ref}", facts=item["facts"], text=item["text"], caller="script")
        )

    service.plant(MEAL, actor=DANA)
    asks = {ref: decide(ref) for ref in ("EXP-1031", "EXP-1032", "EXP-1033", "EXP-1034", "EXP-1035", "EXP-1036")}
    assert {a["verdict"] for a in asks.values()} == {"ask"}

    out = remember(asks["EXP-1031"]["decision_id"])
    assert out["precedent"]["text"] == EXPECTED_TEXT
    assert out["replay"] == {"cases_replayed": 13, "false_automatic_actions": 0, "passed": True}
    assert len(out["resolved"]) == 3  # EXP-1032, EXP-1033, EXP-1036

    assert decide("EXP-1037")["verdict"] == "ask"  # no receipt
    assert [decide(ref)["verdict"] for ref in ("EXP-1038", "EXP-1039", "EXP-1040")] == ["follow"] * 3

    service.overrule(out["precedent"]["id"], reason="finance wants to see these again", actor=DANA)
    after = decide("EXP-1041")
    assert after["verdict"] == "ask" and after["reasons"] == [NO_PRECEDENT]


# ------------------------------------------------------------- 4 and 5


def test_a_busy_database_is_a_503_to_retry_and_reset_forgets_planted_workstreams(temp_db, monkeypatch):
    import sqlite3

    from fastapi.testclient import TestClient

    from app import config
    from app.harness import engines
    from app.harness import service as harness_service
    from app.main import app

    headers = {"Authorization": f"Bearer {config.get_settings().api_token}"}
    body = {"workstream": MEAL, "case_ref": "EXP-1031", "idempotency_key": "evt-busy"}
    with TestClient(app) as client:
        service.plant(MEAL, actor=DANA)

        def busy(request, *, resolves=None):
            raise sqlite3.OperationalError("database is locked")

        with monkeypatch.context() as patch:
            patch.setattr(harness_service, "decide", busy)
            response = client.post("/v1/decide", json=body, headers=headers)
        assert response.status_code == 503 and response.headers["retry-after"] == "2"
        assert "retry with the same idempotency key" in response.json()["detail"]

        assert MEAL in engines.families()
        assert client.post("/demo/reset", follow_redirects=False).status_code == 303
        assert MEAL not in engines.families() and MEAL not in domains.families()
        assert {"returns.missing_accessory", "ap.invoice_variance"} <= set(engines.families())
