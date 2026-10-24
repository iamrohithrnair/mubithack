"""Construct the one Precedenz Strands agent.

There is exactly one agent. It is invoked three times along the golden path —
investigate, produce a decision card, propose a policy — with the same tools and
a task-specific system prompt each time. Extra agents would add moving parts
without adding capability.
"""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.tools.executors import SequentialToolExecutor

from app import audit, mubit
from app.agent import prompts
from app.agent.providers import resolve_model
from app.agent.tools import AgentContext, build_tools

AGENT_NAME = "precedenz-returns-agent"

# Audit event for a workstream whose Mubit task key moved. A plain string, like
# every audit event type; AuditEventType has no entry for it yet.
TASK_KEY_CHANGED = "mubit.task_key_changed"


def build_agent(
    ctx: AgentContext,
    *,
    system_prompt: str,
    model: Any | None = None,
    tools: list[Any] | None = None,
) -> Agent:
    agent = Agent(
        model=model
        or (resolve_model(call_log=ctx.call_log) if ctx.call_log is not None else resolve_model()),
        tools=tools if tools is not None else build_tools(ctx),
        system_prompt=system_prompt,
        name=AGENT_NAME,
        callback_handler=None,
        # Every tool shares the one database connection in `ctx`, and by default
        # Strands runs the tool calls from a single model turn concurrently.
        # Concurrent use of one connection mixes result rows between queries, so
        # tools run one at a time.
        tool_executor=SequentialToolExecutor(),
    )
    if ctx.call_log is not None and getattr(ctx.call_log, "task_key", None) is None:
        _key_task(ctx, agent)
    return agent


def _key_task(ctx: AgentContext, agent: Agent) -> None:
    """Put the decision's Mubit task key on its call log, and note it for the workstream.

    The first agent built for a decision is the decision's task: the
    investigation for returns, the reader for a planted workstream. An agent
    built after it for the same decision, such as a decision card, is a task of
    its own and is not noted, or the workstream's key would flip on every case.
    The specs are the ones Strands sends with each request.

    The first decision to see a changed key records it in the audit log once:
    the examples Mubit kept under the old key no longer count toward this task.
    """
    call_log = ctx.call_log
    key = mubit.task_key(
        _model_id(agent.model),
        agent.system_prompt or "",
        agent.tool_registry.get_all_tool_specs(),
    )
    call_log.task_key = key
    if call_log.family and mubit.note_task_key(call_log.family, key):
        info = mubit.task_key_info(call_log.family) or {}
        audit.record(
            ctx.conn,
            trace_id=ctx.trace_id,
            event_type=TASK_KEY_CHANGED,
            actor="system",
            case_id=ctx.case_id,
            exception_id=ctx.exception_id,
            payload={"workstream": call_log.family, "task_key": key, "previous": info.get("previous")},
        )


def _model_id(model: Any) -> str:
    try:
        config = model.get_config()
    except Exception:
        return ""
    return str(config.get("model_id") or "") if isinstance(config, dict) else ""


def investigation_agent(ctx: AgentContext, *, model: Any | None = None) -> Agent:
    all_tools = build_tools(ctx)
    read_only = [t for t in all_tools if t.tool_name != "replay_candidate_policy"]
    return build_agent(ctx, system_prompt=prompts.INVESTIGATION_PROMPT, model=model, tools=read_only)


def decision_card_agent(ctx: AgentContext, *, model: Any | None = None) -> Agent:
    return build_agent(ctx, system_prompt=prompts.DECISION_CARD_PROMPT, model=model, tools=[])


def policy_proposal_agent(ctx: AgentContext, *, model: Any | None = None) -> Agent:
    all_tools = build_tools(ctx)
    replay_only = [t for t in all_tools if t.tool_name == "replay_candidate_policy"]
    return build_agent(
        ctx, system_prompt=prompts.POLICY_PROPOSAL_PROMPT, model=model, tools=replay_only
    )
