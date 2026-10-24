"""Every way in: the Python client, the `precedenz` CLI, the MCP server, the traffic script."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest

from precedenz_client import cli, client as client_module
from precedenz_client.client import (
    ApprovalRequired,
    AuthError,
    Conflict,
    InvalidRequest,
    NotFound,
    Precedenz,
    Unreachable,
)

ROOT = Path(__file__).resolve().parents[1]

FOLLOW = {
    "decision_id": "dec_1", "verdict": "follow", "workstream": "expenses.meal_claim", "case_ref": "EXP-1",
    "precedent": {"id": "pol_1", "version": 1, "decided_by": "dana.r@wharf-canary-optics.example",
                  "text": "Approve meal claims up to $75 with a receipt.", "origin_case": "EXP-0"},
    "actions": [{"type": "answer", "answer": "approve", "max_amount_usd": 75.0}],
    "reasons": [],
    "checks": [{"name": "matches precedent v1", "ok": True, "detail": ""}],
    "model_calls": [{"via": "mubit", "request_id": "mreq_1", "requested_model": "gpt-6-luna", "ms": 900}],
    "serving_identity": "not reported by the gateway",
}
ASK = {
    "decision_id": "dec_2", "verdict": "ask", "workstream": "expenses.meal_claim", "case_ref": "EXP-2",
    "precedent": None, "actions": [], "reasons": ["amount_usd 140.00 is above the precedent's 75.00"],
    "checks": [{"name": "matches precedent v1", "ok": False, "detail": "140.00 > 75.00"}],
    "model_calls": [],
}


def _mock(handler, **kw) -> Precedenz:
    http = httpx.Client(base_url="http://precedenz.test", transport=httpx.MockTransport(handler))
    return Precedenz("http://precedenz.test", api_token="api-tok", caller="pipeline", http=http, **kw)


# ------------------------------------------------------------------ client


def test_decide_sends_the_case_with_the_api_token_and_a_key_per_case():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=FOLLOW)

    p = _mock(handler)
    receipt = p.decide("expenses.meal_claim", "EXP-1", facts={"amount_usd": 62.4}, text="lunch")
    assert receipt["verdict"] == "follow"
    request = seen[0]
    assert request.method == "POST" and request.url.path == "/v1/decide"
    assert request.headers["authorization"] == "Bearer api-tok"
    assert request.headers["x-precedenz-caller"] == "pipeline"
    assert "x-approval-token" not in request.headers
    body = json.loads(request.content)
    assert body == {"workstream": "expenses.meal_claim", "case_ref": "EXP-1",
                    "idempotency_key": "case:EXP-1", "facts": {"amount_usd": 62.4}, "text": "lunch"}
    p.decide("expenses.meal_claim", "EXP-1", idempotency_key="evt-9")
    assert json.loads(seen[1].content)["idempotency_key"] == "evt-9"


def test_person_calls_carry_the_approval_token_or_are_never_sent():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"answer_id": "ans_1", "precedent": None, "replay": None, "resolved": []})

    without = _mock(handler)
    with pytest.raises(ApprovalRequired):
        without.answer("dec_2", "approve", remember=True)
    with pytest.raises(ApprovalRequired):
        without.overrule("pol_1", "wrong")
    with pytest.raises(ApprovalRequired):
        without.plant("expenses.meal_claim")
    assert seen == []

    person = _mock(handler, approval_token="appr-tok")
    person.answer("dec_2", "approve", remember=True, up_to={"amount_usd": 75}, ignore=["client_meeting"])
    person.overrule("pol_1", "too generous")
    person.plant("expenses.meal_claim")
    answer, overrule, plant = seen
    assert answer.url.path == "/v1/decisions/dec_2/answer"
    assert answer.headers["x-approval-token"] == "appr-tok"
    assert json.loads(answer.content) == {"answer": "approve", "remember": True, "note": "",
                                          "up_to": {"amount_usd": 75}, "ignore": ["client_meeting"]}
    assert overrule.url.path == "/v1/precedents/pol_1/overrule"
    assert json.loads(overrule.content) == {"reason": "too generous"}
    assert plant.url.path == "/v1/workstreams"
    assert json.loads(plant.content) == {"template_id": "expenses.meal_claim"}


@pytest.mark.parametrize(
    ("status", "detail", "error"),
    [
        (401, "missing or invalid API token", AuthError),
        (403, "this needs a person", ApprovalRequired),
        (404, "unknown workstream 'x.y'", NotFound),
        (409, "idempotency key reused with different input", Conflict),
        (422, [{"loc": ["body", "case_ref"], "msg": "Field required"}], InvalidRequest),
    ],
)
def test_refusals_become_typed_errors_with_the_reason(status, detail, error):
    p = _mock(lambda request: httpx.Response(status, json={"detail": detail}))
    with pytest.raises(error) as caught:
        p.decide("x.y", "C-1")
    assert caught.value.status_code == status
    expected = detail if isinstance(detail, str) else "case_ref: Field required"
    assert str(caught.value) == expected


def test_an_unreachable_server_is_its_own_error():
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(Unreachable):
        _mock(handler).workstreams()


def test_lists_accept_a_bare_list_or_a_wrapped_one():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/decisions":
            assert dict(request.url.params) == {"verdict": "ask", "open": "true", "workstream": "w.x"}
            return httpx.Response(200, json={"decisions": [ASK]})
        return httpx.Response(200, json=[{"id": "w.x"}])

    p = _mock(handler)
    assert p.open_questions("w.x") == [ASK]
    assert p.workstreams() == [{"id": "w.x"}]
    assert p.templates() == [{"id": "w.x"}]


def test_events_parse_the_stream_and_skip_heartbeats():
    body = (
        "retry: 3000\n: connected at seq 0\n\n"
        'id: 1\ndata: {"seq": 1, "type": "decision.started", "case_ref": "EXP-1"}\n\n'
        ": heartbeat\n\n"
        'id: 2\ndata: {"seq": 2, "type": "decision.finished", "verdict": "ask"}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/map/events" and request.url.params["since"] == "0"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    events = list(_mock(handler).events(since=0))
    assert [e["seq"] for e in events] == [1, 2]
    assert events[1]["verdict"] == "ask"


def test_the_client_reads_the_real_map_routes(temp_db):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as http:
        p = Precedenz("http://testserver", api_token="unused", http=http)
        snapshot = p.snapshot()
        assert any(ws["id"] == "returns.missing_accessory" for ws in snapshot["workstreams"])
        with pytest.raises(NotFound):
            p._request("GET", "/map/receipt/dec_missing")


@pytest.fixture()
def live_server(temp_db, monkeypatch):
    """The real app on a real socket, for the event stream end to end."""
    import uvicorn

    from app.api import map as map_api
    from app.harness import events
    from app.main import app

    monkeypatch.setattr(map_api, "HEARTBEAT_SECONDS", 0.2)
    events.clear()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=2))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)
    events.clear()


def test_the_client_follows_live_events_from_a_running_server(live_server):
    from app.harness import events

    p = Precedenz(live_server, api_token="unused")
    events.publish("workstream.planted", workstream="expenses.meal_claim", label="Meals", audience="team")

    def later():
        time.sleep(0.3)
        events.publish("decision.finished", decision_id="dec_7", workstream="expenses.meal_claim",
                       case_ref="EXP-7", verdict="follow", precedent_id="pol_1", caller="cli")

    threading.Thread(target=later, daemon=True).start()
    received = []
    for event in p.events(since=0, read_timeout=10):
        received.append(event)
        if len(received) == 2:
            break
    p.close()
    assert [e["type"] for e in received] == ["workstream.planted", "decision.finished"]
    assert received[1]["seq"] == 2 and received[1]["decision_id"] == "dec_7"

    with httpx.stream("GET", f"{live_server}/map/events", headers={"Last-Event-ID": "1"}, timeout=10) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        frames = []
        for line in r.iter_lines():
            frames.append(line)
            if line.startswith("data:"):
                break
    assert "id: 2" in frames


# --------------------------------------------------------------------- CLI


class FakeClient:
    def __init__(self, decide=FOLLOW, error=None, events=()):
        self.calls = []
        self._decide = decide
        self._error = error
        self._events = events
        self.url = "http://fake"

    def decide(self, *args, **kwargs):
        self.calls.append(("decide", args, kwargs))
        if self._error:
            raise self._error
        return self._decide

    def answer(self, *args, **kwargs):
        self.calls.append(("answer", args, kwargs))
        return {"answer_id": "ans_1",
                "precedent": {"id": "pol_9", "version": 1, "status": "active",
                              "text": "Approve meal claims up to $75 with a receipt."},
                "replay": {"passed": True, "cases_replayed": 12, "false_automatic_actions": 0},
                "resolved": [{"decision_id": "dec_5", "case_ref": "EXP-5", "verdict": "follow"}]}

    def events(self, since=None):
        self.calls.append(("events", (), {"since": since}))
        yield from self._events

    def close(self):
        pass


@pytest.fixture()
def fake(monkeypatch):
    holder = {"client": FakeClient()}
    monkeypatch.setattr(cli, "make_client", lambda args: holder["client"])
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    return holder


def test_cli_decide_prints_the_verdict_card(fake, capsys, tmp_path):
    facts = tmp_path / "facts.json"
    facts.write_text('{"amount_usd": 62.4, "receipt_attached": true}')
    code = cli.main(["decide", "expenses.meal_claim", "EXP-1", "--facts", f"@{facts}", "--text", "lunch"])
    out = capsys.readouterr().out
    assert code == 0
    assert out.splitlines()[0].strip() == "FOLLOW  expenses.meal_claim · EXP-1"
    assert '"Approve meal claims up to $75 with a receipt."' in out
    assert "decided by Dana R." in out
    assert "✓ matches precedent v1" in out
    assert "mreq_1 · gpt-6-luna · 900 ms" in out
    assert "answer approve · max_amount_usd 75.00" in out
    _, args, kwargs = fake["client"].calls[0]
    assert args == ("expenses.meal_claim", "EXP-1")
    assert kwargs["facts"] == {"amount_usd": 62.4, "receipt_attached": True}
    assert kwargs["text"] == "lunch" and kwargs["idempotency_key"] is None


def test_cli_decide_shows_why_it_asks(fake, capsys):
    fake["client"] = FakeClient(decide=ASK)
    assert cli.main(["decide", "expenses.meal_claim", "EXP-2", "--facts", '{"amount_usd": 140}']) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].strip() == "ASK  expenses.meal_claim · EXP-2"
    assert "• amount_usd 140.00 is above the precedent's 75.00" in out
    assert "✗ matches precedent v1" in out
    assert "precedenz answer dec_2" in out


def test_cli_json_flag_prints_the_raw_receipt(fake, capsys):
    assert cli.main(["--json", "decide", "expenses.meal_claim", "EXP-1"]) == 0
    assert json.loads(capsys.readouterr().out)["decision_id"] == "dec_1"
    assert cli.main(["decide", "expenses.meal_claim", "EXP-1", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["decision_id"] == "dec_1"
    args = cli.build_parser().parse_args(["--url", "http://a", "decide", "w.x", "C-1"])
    assert (args.url, args.json, args.caller) == ("http://a", False, "cli")
    args = cli.build_parser().parse_args(["decide", "w.x", "C-1", "--url", "http://b", "--caller", "ci"])
    assert (args.url, args.caller) == ("http://b", "ci")


def test_cli_answer_passes_the_widening_and_shows_the_precedent(fake, capsys):
    code = cli.main(["answer", "dec_2", "approve", "--remember", "--up-to", "amount_usd=75",
                     "--ignore", "client_meeting", "attendees", "--note", "client lunches are fine"])
    out = capsys.readouterr().out
    assert code == 0
    _, args, kwargs = fake["client"].calls[0]
    assert args == ("dec_2", "approve")
    assert kwargs == {"remember": True, "up_to": {"amount_usd": 75},
                      "ignore": ["client_meeting", "attendees"], "note": "client lunches are fine"}
    assert "DECIDED ONCE" in out
    assert "12 past answers · 0 contradictions · passed" in out
    assert "1 waiting question resolved on its own" in out


def test_cli_reports_a_refusal_on_stderr(fake, capsys):
    fake["client"] = FakeClient(error=NotFound("unknown workstream 'x.y'", status_code=404))
    assert cli.main(["decide", "x.y", "C-1"]) == 1
    assert "unknown workstream 'x.y' (404)" in capsys.readouterr().err


def test_cli_defaults_to_the_demo_tokens_and_says_so(monkeypatch, capsys):
    for name in ("PRECEDENZ_URL", "PRECEDENZ_API_TOKEN", "PRECEDENZ_APPROVAL_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    p = cli.make_client(cli.build_parser().parse_args(["answer", "dec_1", "approve"]))
    assert p.url == "http://127.0.0.1:8000"
    assert p.api_token == "replace-me-local-api-token"
    assert p.approval_token == "replace-me-local-demo-token"
    err = capsys.readouterr().err
    assert "PRECEDENZ_API_TOKEN is not set" in err and "PRECEDENZ_APPROVAL_TOKEN is not set" in err

    monkeypatch.setenv("PRECEDENZ_API_TOKEN", "real-api")
    monkeypatch.setenv("PRECEDENZ_URL", "http://precedenz.internal:9000")
    p = cli.make_client(cli.build_parser().parse_args(["decide", "w.x", "C-1"]))
    assert (p.url, p.api_token, p.approval_token) == ("http://precedenz.internal:9000", "real-api", None)
    assert capsys.readouterr().err == ""


def test_cli_watch_prints_one_line_per_event(fake, capsys):
    stream = [
        {"seq": 1, "at": "2026-10-25T01:14:07Z", "type": "decision.started",
         "workstream": "expenses.meal_claim", "case_ref": "EXP-1", "caller": "script"},
        {"seq": 2, "at": "2026-10-25T01:14:08Z", "type": "model.call", "workstream": "expenses.meal_claim",
         "case_ref": "EXP-1", "request_id": "mreq_1", "requested_model": "gpt-6-luna", "ms": 900},
        {"seq": 3, "at": "2026-10-25T01:14:09Z", "type": "decision.finished", "verdict": "follow",
         "workstream": "expenses.meal_claim", "case_ref": "EXP-1", "precedent_id": "pol_1"},
        {"seq": 4, "at": "2026-10-25T01:14:10Z", "type": "precedent.overruled", "precedent_id": "pol_1",
         "by": "dana.r@wharf-canary-optics.example", "reason": "too generous"},
    ]
    fake["client"] = FakeClient(events=stream)
    assert cli.watch(fake["client"], cli.Paint(False), max_events=4) == 0
    lines = capsys.readouterr().out.splitlines()[1:]
    assert "DECIDE" in lines[0] and "expenses.meal_claim · EXP-1" in lines[0]
    assert "model" in lines[1] and "mreq_1" in lines[1]
    assert "FOLLOW" in lines[2] and "pol_1" in lines[2]
    assert "OVERRULE" in lines[3] and "by Dana R." in lines[3]


# --------------------------------------------------------------------- MCP


def test_the_mcp_server_can_ask_but_never_answer():
    from precedenz_client import mcp_server

    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {tool.name for tool in tools}
    assert names == {"decide", "get_receipt", "list_open_questions", "list_workstreams"}
    for forbidden in ("answer", "remember", "overrule", "plant"):
        assert not any(forbidden in name for name in names)
    decide = next(tool for tool in tools if tool.name == "decide")
    assert "follow" in decide.description and "ask" in decide.description


def test_the_mcp_server_never_carries_the_approval_token(monkeypatch):
    from precedenz_client import mcp_server

    monkeypatch.setenv("PRECEDENZ_APPROVAL_TOKEN", "a-real-approval-token")
    monkeypatch.setattr(mcp_server, "_client", None)
    assert mcp_server.client().approval_token is None
    assert mcp_server.client().caller == "agent"
    source = (ROOT / "precedenz_client" / "mcp_server.py").read_text()
    assert "os.environ.get(\"PRECEDENZ_APPROVAL_TOKEN\"" not in source


def test_the_mcp_decide_tool_says_what_to_do(monkeypatch):
    from precedenz_client import mcp_server

    monkeypatch.setattr(mcp_server, "_client", FakeClient(decide=ASK))
    result = mcp_server.decide("expenses.meal_claim", "EXP-2", facts={"amount_usd": 140})
    assert result["verdict"] == "ask"
    assert result["what_to_do"].startswith("ASK: a person must decide")
    assert "amount_usd 140.00 is above" in result["what_to_do"]


# ---------------------------------------------------------- traffic script


@pytest.fixture()
def traffic():
    spec = importlib.util.spec_from_file_location("demo_traffic", ROOT / "scripts" / "demo_traffic.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_traffic_sources_cover_the_built_in_demo_cases(traffic):
    returns = traffic.load_cases("returns.missing_accessory")
    assert [c["case_ref"] for c in returns] == [f"CASE-200{n}" for n in range(1, 10)]
    assert all(set(c) == {"case_ref"} for c in returns)
    assert [c["case_ref"] for c in traffic.load_cases("ap.invoice_variance")] == [
        "AP-2001", "AP-2002", "AP-2003", "AP-2004"]


def test_traffic_sends_template_examples_with_facts_and_text(traffic, tmp_path, monkeypatch):
    (tmp_path / "workstreams").mkdir()
    (tmp_path / "workstreams" / "expenses.meal_claim.json").write_text(json.dumps({
        "contract": {}, "history": [],
        "incoming": [{"case_ref": f"EXP-{n}", "facts": {"amount_usd": n}, "text": f"lunch {n}"} for n in range(5)],
    }))
    monkeypatch.setattr(traffic, "FIXTURES", tmp_path)
    cases = traffic.load_cases("expenses.meal_claim")
    assert cases[1] == {"case_ref": "EXP-1", "facts": {"amount_usd": 1}, "text": "lunch 1"}
    assert traffic.load_cases("expenses.meal_claim", send="facts")[1] == {"case_ref": "EXP-1", "facts": {"amount_usd": 1}}

    fake = FakeClient()
    code = traffic.main(["--workstream", "expenses.meal_claim", "--start", "1", "--count", "2", "--pace", "0"],
                        client=fake, sleep=lambda s: None)
    assert code == 0
    sent = [(args[1], kwargs) for _, args, kwargs in fake.calls]
    assert sent == [("EXP-1", {"facts": {"amount_usd": 1}, "text": "lunch 1"}),
                    ("EXP-2", {"facts": {"amount_usd": 2}, "text": "lunch 2"})]


def test_traffic_stops_at_the_model_call_budget(traffic, capsys):
    two_calls = dict(FOLLOW, model_calls=[{"request_id": "a"}, {"request_id": "b"}])
    fake = FakeClient(decide=two_calls)
    code = traffic.main(["--workstream", "returns.missing_accessory", "--count", "9", "--pace", "0",
                         "--max-model-calls", "3"], client=fake, sleep=lambda s: None)
    assert code == 0
    assert [args[1] for _, args, _ in fake.calls] == ["CASE-2001", "CASE-2002"]
    out = capsys.readouterr().out
    assert "budget reached: 4 model calls; stopping before CASE-2003" in out
    assert out.count("FOLLOW") == 2
