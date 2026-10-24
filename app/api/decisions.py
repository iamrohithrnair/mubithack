"""/v1 decision routes: decide, receipts, open questions, outcomes.

Every route takes the API token. None of them needs a person: deciding only
ever follows a precedent a person already made, or asks. A model, tool or
Sapling failure is never an error here; it comes back as an ask with the reason.

  401  missing or wrong API token
  404  unknown workstream or decision
  409  idempotency key reused with a different input
  422  malformed body
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api import caller_label, require_api_token
from app.harness import service
from app.harness.engines import DecideRequest

router = APIRouter(prefix="/v1", tags=["decisions"], dependencies=[Depends(require_api_token)])


class DecideBody(BaseModel):
    """One case. `facts` and `text` apply to planted workstreams only; built-in
    workstreams re-derive everything from their source systems by `case_ref`."""

    model_config = ConfigDict(extra="ignore")

    workstream: str = Field(min_length=1, max_length=120)
    case_ref: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)
    facts: dict[str, Any] | None = None
    text: str | None = Field(default=None, max_length=20_000)
    source_revision: str | None = Field(default=None, max_length=200)


class OutcomeBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["executed", "failed"]
    detail: str = Field(default="", max_length=2_000)
    actor: str | None = Field(default=None, max_length=120)


@router.post("/decide")
def decide(body: DecideBody, caller: str = Depends(caller_label)) -> dict[str, Any]:
    """Follow or ask for one case. Synchronous: a returns case waits for its model call."""
    request = DecideRequest(
        workstream=body.workstream,
        case_ref=body.case_ref,
        idempotency_key=body.idempotency_key,
        facts=body.facts,
        text=body.text,
        source_revision=body.source_revision,
        caller=caller,
    )
    try:
        return service.decide(request)
    except service.UnknownWorkstream as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from None
    except service.IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/decisions")
def list_decisions(
    verdict: Literal["follow", "ask"] | None = None,
    open: bool = False,  # noqa: A002 - the query parameter is called `open`
    workstream: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """Recent receipts, or with `open=true` the asks still waiting for a person."""
    if open:
        decisions = [] if verdict == "follow" else service.open_questions(workstream)
    else:
        decisions = service.list_decisions(workstream=workstream, verdict=verdict, limit=limit)
    return {"decisions": decisions[:limit], "count": len(decisions[:limit])}


@router.get("/decisions/{decision_id}")
def get_decision(decision_id: str) -> dict[str, Any]:
    """The receipt, with its answers, outcomes, and the decision that resolved it, if any."""
    receipt = service.get_receipt(decision_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail=f"no decision '{decision_id}'")
    return receipt


@router.post("/decisions/{decision_id}/outcome")
def report_outcome(
    decision_id: str, body: OutcomeBody, caller: str = Depends(caller_label)
) -> dict[str, Any]:
    """The caller reports what happened when it carried the decision out. Recorded as reported."""
    try:
        return service.report_outcome(
            decision_id, body.status, body.detail, body.actor or caller
        )
    except service.UnknownDecision as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from None
