"""How a decision's model calls reach Mubit, and what Precedenz keeps about them.

Every gateway response's `x-middleware-request-id` lands on the decision's call
log and on the map's event stream; each request carries Mubit's session field;
the key never appears in either. The gateway here is an `httpx.MockTransport`
under the real Strands model and the real OpenAI SDK: no network, no spend.
"""

from __future__ import annotations

import dataclasses
import json

import httpx
import pytest
from strands.models.openai_responses import OpenAIResponsesModel

from app import audit, mubit
from app.agent import build, prompts
from app.agent.build import decision_card_agent, investigation_agent
from app.agent.providers import CallLog, ModelCall, RecordingResponsesModel, resolve_model
from app.agent.tools import AgentContext
from app.config import get_settings
from app.harness import events

FAKE_KEY = "mbt_test_not_a_real_key"
FAMILY = "returns.missing_accessory"
MESSAGES = [{"role": "user", "content": [{"text": "Investigate CASE-2002."}]}]
TOOL_SPECS = [
    {
        "name": "get_return_case",
        "description": "Look up a return case.",
        "inputSchema": {"json": {"type": "object", "properties": {"case_id": {"type": "string"}}}},
    }
]


def _sse(*payloads: dict) -> bytes:
    return "".join(f"event: {p['type']}\ndata: {json.dumps(p)}\n\n" for p in payloads).encode()


# The smallest Responses API stream Strands reads to the end.
COMPLETED_STREAM = _sse(
    {"type": "response.created", "sequence_number": 0,
     "response": {"id": "resp_test", "object": "response", "status": "in_progress", "output": []}},
    {"type": "response.output_text.delta", "sequence_number": 1, "item_id": "msg_test",
     "output_index": 0, "content_index": 0, "delta": "ok", "logprobs": []},
    {"type": "response.completed", "sequence_number": 2,
     "response": {"id": "resp_test", "object": "response", "status": "completed", "output": []}},
)


def ok(request_id: str | None) -> tuple[int, dict, bytes]:
    headers = {"content-type": "text/event-stream"}
    if request_id is not None:
        headers["x-middleware-request-id"] = request_id
    return 200, headers, COMPLETED_STREAM


def gateway_error(request_id: str) -> tuple[int, dict, bytes]:
    body = {"error": {"message": "upstream unavailable", "type": "middleware_gateway_error"}}
    # retry-after-ms keeps the SDK's retry pause at a millisecond.
    headers = {"x-middleware-request-id": request_id, "retry-after-ms": "1"}
    return 500, headers, json.dumps(body).encode()


class Gateway:
    """Stands in for the Mubit gateway: scripted replies, and every request kept."""

    def __init__(self, *replies: tuple[int, dict, bytes]) -> None:
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, headers, content = self.replies.pop(0)
        return httpx.Response(status, headers=headers, content=content)

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("MUBIT_API_KEY", FAKE_KEY)
    events.clear()
    mubit.forget_task_keys()
    yield
    mubit.forget_task_keys()


def _settings():
    return dataclasses.replace(get_settings(), model_provider="mubit")


def _recording_model(gateway: Gateway, call_log: CallLog, **overrides) -> RecordingResponsesModel:
    model = resolve_model(
        _settings(), call_log=call_log, transport=httpx.MockTransport(gateway), **overrides
    )
    assert isinstance(model, RecordingResponsesModel)
    return model


async def _drain(model, *, tool_specs=None, system_prompt=None) -> list:
    return [e async for e in model.stream(MESSAGES, tool_specs, system_prompt)]


def _model_call_events() -> list[dict]:
    return [e for e in events.since(0) if e["type"] == "model.call"]


# ------------------------------------------------------------ request IDs


async def test_each_gateway_response_is_recorded_with_its_request_id():
    gateway = Gateway(ok("mw-req-1"), ok("mw-req-2"))
    log = CallLog(FAMILY, "CASE-2002")
    model = _recording_model(gateway, log)

    await _drain(model)
    await _drain(model)

    assert [c.request_id for c in log] == ["mw-req-1", "mw-req-2"]
    assert [c.status for c in log] == [200, 200]
    assert {c.requested_model for c in log} == {"gpt-6-luna"}
    assert all(isinstance(c.ms, int) and c.ms >= 0 for c in log)


async def test_a_retried_request_records_each_attempt():
    gateway = Gateway(gateway_error("mw-req-failed"), ok("mw-req-retry"))
    log = CallLog(FAMILY, "CASE-2002")

    await _drain(_recording_model(gateway, log))

    assert len(gateway.requests) == 2  # the SDK retried once, on its own
    assert [(c.request_id, c.status) for c in log] == [("mw-req-failed", 500), ("mw-req-retry", 200)]


async def test_a_response_without_the_header_records_no_request_id():
    log = CallLog(FAMILY, "CASE-2002")

    await _drain(_recording_model(Gateway(ok(None)), log))

    assert log == [ModelCall(request_id=None, requested_model="gpt-6-luna", status=200, ms=log[0].ms)]


async def test_every_request_gets_a_fresh_client_that_is_closed_after(monkeypatch):
    clients: list[httpx.AsyncClient] = []
    resolve = RecordingResponsesModel._resolve_client_args

    def spy(self):
        args = resolve(self)
        clients.append(args["http_client"])
        return args

    monkeypatch.setattr(RecordingResponsesModel, "_resolve_client_args", spy)
    model = _recording_model(Gateway(ok("mw-req-1"), ok("mw-req-2")), CallLog(FAMILY, "CASE-2002"))

    await _drain(model)
    await _drain(model)

    assert len(clients) == 2 and clients[0] is not clients[1]
    assert all(client.is_closed for client in clients)


async def test_model_call_events_name_the_workstream_and_case():
    log = CallLog(FAMILY, "CASE-2002")

    await _drain(_recording_model(Gateway(gateway_error("mw-req-1"), ok("mw-req-2")), log))

    published = _model_call_events()
    assert [e["request_id"] for e in published] == ["mw-req-1", "mw-req-2"]
    for event in published:
        assert event["workstream"] == FAMILY
        assert event["case_ref"] == "CASE-2002"
        assert event["requested_model"] == "gpt-6-luna"
        assert isinstance(event["ms"], int)


async def test_the_key_reaches_the_gateway_and_nothing_else():
    gateway = Gateway(gateway_error("mw-req-1"), ok("mw-req-2"))
    log = CallLog(FAMILY, "CASE-2002")

    await _drain(_recording_model(gateway, log))

    assert all(r.headers["authorization"] == f"Bearer {FAKE_KEY}" for r in gateway.requests)
    kept = json.dumps([c.to_dict() for c in log]) + repr(list(log)) + json.dumps(events.since(0))
    assert "mbt_" not in kept
    assert FAKE_KEY not in kept


# ---------------------------------------------------------------- sessions


async def test_requests_carry_the_session_and_keep_every_other_param():
    gateway = Gateway(ok("mw-req-1"))
    params = {"max_output_tokens": 300, "metadata": {"team": "returns"}}
    log = CallLog(FAMILY, "CASE-2002")

    await _drain(
        _recording_model(gateway, log, params=params),
        tool_specs=TOOL_SPECS,
        system_prompt=prompts.INVESTIGATION_PROMPT,
    )

    [body] = gateway.bodies()
    assert str(gateway.requests[0].url) == "https://api.mubit.ai/openai/v1/responses"
    assert body["metadata"] == {"team": "returns", "session_id": "returns.missing_accessory:CASE-2002"}
    assert body["max_output_tokens"] == 300
    assert body["model"] == "gpt-6-luna"
    assert body["instructions"] == prompts.INVESTIGATION_PROMPT
    assert [t["name"] for t in body["tools"]] == ["get_return_case"]
    assert params == {"max_output_tokens": 300, "metadata": {"team": "returns"}}  # not mutated


def test_recording_changes_nothing_in_the_request_but_the_session():
    plain = resolve_model(_settings())
    recording = resolve_model(_settings(), call_log=CallLog(FAMILY, "CASE-2002"))

    before = plain._format_request(MESSAGES, TOOL_SPECS, prompts.INVESTIGATION_PROMPT)
    after = recording._format_request(MESSAGES, TOOL_SPECS, prompts.INVESTIGATION_PROMPT)

    assert type(plain) is OpenAIResponsesModel
    assert "metadata" not in before
    assert "http_client" not in plain._resolve_client_args()
    assert after.pop("metadata") == {"session_id": "returns.missing_accessory:CASE-2002"}
    assert after == before


async def test_recording_client_uses_the_model_timeout():
    model = resolve_model(_settings(), call_log=CallLog(FAMILY, "CASE-2002"))

    client = model._resolve_client_args()["http_client"]
    await client.aclose()

    assert client.timeout.read == float(get_settings().model_timeout_seconds)


# ---------------------------------------------------------------- task key


def test_task_key_is_stable_and_moves_with_what_mubit_fingerprints():
    key = mubit.task_key("gpt-6-luna", "Investigate.", TOOL_SPECS)
    other_tool = {**TOOL_SPECS[0], "name": "lookup_replacement_cost"}

    assert key == mubit.task_key("gpt-6-luna", "Investigate.", [dict(TOOL_SPECS[0])])
    assert len(key) == 16 and int(key, 16) >= 0
    assert mubit.task_key("gpt-6-luna", "Investigate.", [TOOL_SPECS[0], other_tool]) == mubit.task_key(
        "gpt-6-luna", "Investigate.", [other_tool, TOOL_SPECS[0]]
    )
    assert key != mubit.task_key("gpt-6-luna", "Investigate carefully.", TOOL_SPECS)
    assert key != mubit.task_key("gpt-6-mini", "Investigate.", TOOL_SPECS)
    assert key != mubit.task_key("gpt-6-luna", "Investigate.", [other_tool])
    assert key != mubit.task_key("gpt-6-luna", "Investigate.", TOOL_SPECS, {"type": "object"})


def test_a_changed_key_is_flagged_and_stays_flagged():
    assert mubit.task_key_info(FAMILY) is None
    assert mubit.note_task_key(FAMILY, "aaaa") is False
    assert mubit.note_task_key(FAMILY, "aaaa") is False
    assert mubit.task_key_info(FAMILY)["changed"] is False

    assert mubit.note_task_key(FAMILY, "bbbb") is True
    assert mubit.note_task_key(FAMILY, "bbbb") is False

    info = mubit.task_key_info(FAMILY)
    assert info["value"] == "bbbb" and info["changed"] is True and info["previous"] == "aaaa"
    assert info["since"].endswith("Z")


def test_build_keys_the_decisions_task_from_model_prompt_and_tools(conn):
    log = CallLog(FAMILY, "CASE-2001")
    ctx = AgentContext(conn=conn, trace_id="trc_mubit", case_id="CASE-2001", call_log=log)

    agent = investigation_agent(ctx)

    expected = mubit.task_key(
        "test-model-v1", prompts.INVESTIGATION_PROMPT, agent.tool_registry.get_all_tool_specs()
    )
    assert log.task_key == expected
    assert mubit.task_key_info(FAMILY)["value"] == expected
    assert _key_change_notes(conn) == []  # a first sighting is not a change


def _key_change_notes(conn) -> list:
    return [e for e in audit.read_all(conn) if e.event_type == build.TASK_KEY_CHANGED]


def test_a_changed_key_is_noted_once_in_the_audit_log(conn):
    mubit.note_task_key(FAMILY, "an-older-key")
    logs = []
    for case_id in ("CASE-2001", "CASE-2002"):
        logs.append(CallLog(FAMILY, case_id))
        ctx = AgentContext(conn=conn, trace_id=f"trc_{case_id}", case_id=case_id, call_log=logs[-1])
        investigation_agent(ctx)

    [note] = _key_change_notes(conn)
    assert note.trace_id == "trc_CASE-2001" and note.actor == "system"
    assert note.payload == {
        "workstream": FAMILY,
        "task_key": logs[0].task_key,
        "previous": "an-older-key",
    }
    assert mubit.task_key_info(FAMILY)["changed"] is True


def test_a_later_agent_for_the_same_decision_does_not_flip_the_key(conn):
    log = CallLog(FAMILY, "CASE-2001")
    ctx = AgentContext(conn=conn, trace_id="trc_mubit", case_id="CASE-2001", call_log=log)

    investigation_agent(ctx)
    first = log.task_key
    decision_card_agent(ctx)

    info = mubit.task_key_info(FAMILY)
    assert log.task_key == first
    assert (info["value"], info["changed"]) == (first, False)


def test_an_agent_run_records_on_whatever_thread_strands_uses(conn):
    gateway = Gateway(ok("mw-req-1"))
    log = CallLog(FAMILY, "CASE-2001")
    ctx = AgentContext(conn=conn, trace_id="trc_mubit", case_id="CASE-2001", call_log=log)
    agent = build.build_agent(
        ctx, system_prompt="Answer ok.", model=_recording_model(gateway, log), tools=[]
    )

    result = agent("Is this case complete?")

    assert str(result).strip() == "ok"
    assert [c.request_id for c in log] == ["mw-req-1"]
    assert gateway.bodies()[0]["metadata"] == {"session_id": "returns.missing_accessory:CASE-2001"}
    assert log.task_key == mubit.task_key("gpt-6-luna", "Answer ok.", [])


def test_build_without_a_call_log_is_unchanged(conn, monkeypatch):
    from tests.fake_model import DeterministicTestModel

    calls: list[tuple] = []

    def fake_resolve(*args, **kwargs):
        calls.append((args, kwargs))
        return DeterministicTestModel()

    def must_not_run(*_a, **_k):
        raise AssertionError("no task key without a call log")

    monkeypatch.setattr(build, "resolve_model", fake_resolve)
    monkeypatch.setattr(mubit, "task_key", must_not_run)
    monkeypatch.setattr(mubit, "note_task_key", must_not_run)
    ctx = AgentContext(conn=conn, trace_id="trc_mubit", case_id="CASE-2001")

    for factory in (investigation_agent, decision_card_agent, build.policy_proposal_agent):
        factory(ctx)

    assert calls == [((), {})] * 3


# ---------------------------------------------------------- console mirror


def _console(monkeypatch, tmp_path, content: str | None):
    from app import config

    path = tmp_path / "mubit_console.json"
    if content is not None:
        path.write_text(content)
    monkeypatch.setattr(
        config, "settings", dataclasses.replace(get_settings(), mubit_console_snapshot=path)
    )


def _snapshot(stage: str, family: str = FAMILY) -> str:
    entry = {"stage": stage, "detail": "18 of 25 examples", "as_of": "2026-10-24T22:10:00Z"}
    return json.dumps({"workstreams": {family: entry}})


def test_console_stage_mirrors_the_snapshot(monkeypatch, tmp_path):
    _console(monkeypatch, tmp_path, _snapshot("Collecting examples"))

    assert mubit.console_stage(FAMILY) == {
        "stage": "Collecting examples",
        "detail": "18 of 25 examples",
        "source": "Mubit console",
        "as_of": "2026-10-24T22:10:00Z",
    }
    assert mubit.console_stage("ap.invoice_variance") is None


@pytest.mark.parametrize("stage", mubit.STAGES)
def test_console_stage_accepts_each_of_mubits_words(monkeypatch, tmp_path, stage):
    _console(monkeypatch, tmp_path, _snapshot(stage.lower()))

    assert mubit.console_stage(FAMILY)["stage"] == stage


@pytest.mark.parametrize("stage", ["Promoted", "Almost live", "", "Live soon", 3, None])
def test_console_stage_rejects_any_other_stage(monkeypatch, tmp_path, stage):
    _console(monkeypatch, tmp_path, _snapshot(stage))

    assert mubit.console_stage(FAMILY) is None


@pytest.mark.parametrize("content", [None, "", "not json", "[]", '{"workstreams": []}'])
def test_console_stage_is_none_without_a_usable_snapshot(monkeypatch, tmp_path, content):
    _console(monkeypatch, tmp_path, content)

    assert mubit.console_stage(FAMILY) is None
