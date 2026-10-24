"""/v1 workstream routes: templates, workstreams, plant, answer, overrule.

Reading what exists takes the API token. Planting, answering and overruling are
things only a person does, so they also take the approval token, which the MCP
server never carries: your agent can ask; only you can answer.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api import require_api_token, require_approval_token
from app.workstreams import service

router = APIRouter(prefix="/v1", tags=["workstreams"])

# The person the local demo acts for when a request names nobody.
DEFAULT_ACTOR = "dana.r@wharf-canary-optics.example"


def actor_header(x_precedenz_actor: str | None = Header(default=None)) -> str | None:
    return (x_precedenz_actor or "").strip()[:120] or None


class PlantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template: str | None = None
    contract: dict[str, Any] | None = None
    actor: str | None = Field(default=None, max_length=120)


class AnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=40)
    remember: bool = False
    up_to: dict[str, float] | None = None
    ignore: list[str] | None = None
    note: str = Field(default="", max_length=500)
    actor: str | None = Field(default=None, max_length=120)


class OverruleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)
    actor: str | None = Field(default=None, max_length=120)


def _fail(exc: service.WorkstreamError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail())


@router.get("/templates", dependencies=[Depends(require_api_token)])
def get_templates() -> dict[str, Any]:
    return {"templates": service.list_templates()}


@router.get("/workstreams", dependencies=[Depends(require_api_token)])
def get_workstreams() -> dict[str, Any]:
    return {"workstreams": service.list_workstreams()}


@router.post(
    "/workstreams",
    status_code=201,
    dependencies=[Depends(require_api_token), Depends(require_approval_token)],
)
def post_workstream(body: PlantBody, actor: str | None = Depends(actor_header)) -> dict[str, Any]:
    try:
        return service.plant(
            template_id=body.template,
            contract=body.contract,
            actor=body.actor or actor or DEFAULT_ACTOR,
        )
    except service.WorkstreamError as exc:
        raise _fail(exc) from exc


@router.post("/decisions/{decision_id}/answer", dependencies=[Depends(require_api_token)])
def post_answer(
    decision_id: str,
    body: AnswerBody,
    approval_token: str = Depends(require_approval_token),
    actor: str | None = Depends(actor_header),
) -> dict[str, Any]:
    try:
        return service.answer(
            decision_id,
            body.answer,
            remember=body.remember,
            up_to=body.up_to,
            ignore=body.ignore,
            note=body.note,
            actor=body.actor or actor or DEFAULT_ACTOR,
            approval_token=approval_token,
        )
    except service.WorkstreamError as exc:
        raise _fail(exc) from exc


@router.post(
    "/precedents/{precedent_id}/overrule",
    dependencies=[Depends(require_api_token), Depends(require_approval_token)],
)
def post_overrule(
    precedent_id: str, body: OverruleBody, actor: str | None = Depends(actor_header)
) -> dict[str, Any]:
    try:
        return service.overrule(
            precedent_id, reason=body.reason, actor=body.actor or actor or DEFAULT_ACTOR
        )
    except service.WorkstreamError as exc:
        raise _fail(exc) from exc
