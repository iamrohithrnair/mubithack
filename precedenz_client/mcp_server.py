"""`precedenz-mcp`: Precedenz for AI agents, over MCP (stdio).

    claude mcp add precedenz -- precedenz-mcp

Four tools, all of them questions: decide, get_receipt, list_open_questions,
list_workstreams. There is deliberately no tool that answers a question,
remembers an answer, overrules a precedent or plants a workstream. Your agent
can ask; only a person can answer. The server reads PRECEDENZ_URL and
PRECEDENZ_API_TOKEN and never the approval token, so it could not do those
things even if a tool tried.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from precedenz_client.client import DEFAULT_URL, Precedenz, PrecedenzError, Unreachable

DEMO_API_TOKEN = "replace-me-local-api-token"

INSTRUCTIONS = (
    "Precedenz repeats decisions a person already made; it never makes new ones. "
    "Call `decide` for a case in a workstream. A `follow` verdict means a person already "
    "decided cases like this one: carry out exactly the returned actions, nothing more. "
    "An `ask` verdict means a person must decide: tell the user what needs deciding and "
    "why (the reasons), and do not act on the case yourself. You cannot answer, remember, "
    "overrule or plant anything; only a person can, in the Precedenz map or CLI."
)

mcp = FastMCP("precedenz", instructions=INSTRUCTIONS, log_level="WARNING")

_client: Precedenz | None = None


def client() -> Precedenz:
    """One client per server process, built from the environment on first use."""
    global _client
    if _client is None:
        token = os.environ.get("PRECEDENZ_API_TOKEN", "").strip()
        if not token:
            print("precedenz-mcp: PRECEDENZ_API_TOKEN is not set; using the local demo token",
                  file=sys.stderr)
            token = DEMO_API_TOKEN
        url = os.environ.get("PRECEDENZ_URL", "").strip() or DEFAULT_URL
        # No approval token, ever: a person-only call from here fails before it is sent.
        _client = Precedenz(url, api_token=token, approval_token=None, caller="agent")
    return _client


def _what_to_do(receipt: dict[str, Any]) -> str:
    if receipt.get("verdict") == "follow":
        precedent = receipt.get("precedent") or {}
        return (
            "FOLLOW: a person already decided this kind of case "
            f"(\"{precedent.get('text', '')}\"). Carry out exactly the returned actions, "
            "then you are done. Do not widen them."
        )
    reasons = "; ".join(receipt.get("reasons") or []) or "no precedent covers it"
    return (
        f"ASK: a person must decide this case. Tell the user what needs deciding and why: {reasons}. "
        f"Do not act on the case. A person answers it in the Precedenz map or with "
        f"`precedenz answer {receipt.get('decision_id', '<id>')} <answer>`."
    )


def _guard(call):
    try:
        return call()
    except Unreachable as exc:
        raise RuntimeError(f"Precedenz is unreachable: {exc}") from exc
    except PrecedenzError as exc:
        status = f" (HTTP {exc.status_code})" if exc.status_code else ""
        raise RuntimeError(f"Precedenz refused{status}: {exc}") from exc


@mcp.tool(
    description=(
        "Ask Precedenz what to do with one case in a workstream. It returns `follow` (repeat a "
        "decision a person already made; carry out the returned actions, nothing else) or `ask` "
        "(tell the user what a person must decide, and why; do not act on the case). Built-in "
        "workstreams need only the case_ref; planted ones also take typed `facts` and/or the case "
        "as written in `text`. Sending the same case again returns the same decision."
    )
)
def decide(
    workstream: str,
    case_ref: str,
    facts: dict[str, Any] | None = None,
    text: str | None = None,
) -> dict[str, Any]:
    receipt = _guard(lambda: client().decide(workstream, case_ref, facts=facts, text=text))
    return {"what_to_do": _what_to_do(receipt), **receipt}


@mcp.tool(
    description=(
        "Read the receipt of one decision: verdict (follow or ask), the precedent it followed, "
        "the checks that ran, the reasons it asked, any answer a person gave, and whether a "
        "later decision resolved it."
    )
)
def get_receipt(decision_id: str) -> dict[str, Any]:
    return _guard(lambda: client().receipt(decision_id))


@mcp.tool(
    description=(
        "List the questions waiting for a person (asks nobody has answered yet), optionally for "
        "one workstream. Use it to tell the user what needs their decision. You cannot answer "
        "them: only a person can."
    )
)
def list_open_questions(workstream: str | None = None) -> dict[str, Any]:
    questions = _guard(lambda: client().open_questions(workstream))
    return {"count": len(questions), "questions": questions}


@mcp.tool(
    description=(
        "List the workstreams Precedenz can decide for: their ids, the question each answers, "
        "the one answer a precedent may repeat, and the typed fields a case carries."
    )
)
def list_workstreams() -> dict[str, Any]:
    return {"workstreams": _guard(lambda: client().workstreams())}


def main() -> None:
    mcp.run()  # stdio


if __name__ == "__main__":
    main()
