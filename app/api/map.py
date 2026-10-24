"""/map data, live events (SSE), and the map's person-only actions. Owner: agent E.

Reads follow the app's page model: the local demo has no login, so the snapshot,
the event stream and the receipts are open like the inbox is. Writes are the
person's: answering a question and overruling a precedent each need the
approval token, checked here before the workstream service is called, exactly
as policy activation checks it today.

The event stream waits on the in-process bus in a worker thread, so a quiet
stream never holds the event loop; every 15 seconds without news it sends a
comment line, which keeps proxies and browsers from closing the connection.
"""

from __future__ import annotations

import hmac
import json
import logging
from typing import Any, AsyncIterator, Awaitable, Callable

import anyio
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import config
from app.harness import events, map_data

log = logging.getLogger(__name__)

router = APIRouter(tags=["map"])

# The local demo's operator: the person the map acts as when they answer.
OPERATOR = map_data.OPERATOR
HEARTBEAT_SECONDS = 15.0


# ------------------------------------------------------------------ reads


@router.get("/map/data")
def map_snapshot() -> dict[str, Any]:
    """Everything the map draws at rest. Runs in the threadpool: it reads the database."""
    return map_data.snapshot()


@router.get("/map/receipt/{decision_id}")
def map_receipt(decision_id: str) -> dict[str, Any]:
    found = map_data.receipt(decision_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"no decision '{decision_id}'")
    return found


def start_seq(last_event_id: str | None, since: int | None) -> int | None:
    """Where a stream starts: Last-Event-ID wins (a reconnect), then ?since=.

    None means "from now": a fresh connection with neither gets live events only,
    instead of a replay of everything the bus still holds.
    """
    for raw in (last_event_id, since):
        if raw is None or str(raw).strip() == "":
            continue
        try:
            return max(0, int(str(raw).strip()))
        except ValueError:
            continue
    return None


def _frame(event: dict[str, Any]) -> str:
    return f"id: {event['seq']}\ndata: {json.dumps(event, default=str)}\n\n"


async def event_stream(
    since: int | None,
    *,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    heartbeat: float | None = None,
) -> AsyncIterator[str]:
    """SSE frames for every event after `since`, then live ones, until the client goes."""
    interval = HEARTBEAT_SECONDS if heartbeat is None else heartbeat
    seq = events.last_seq() if since is None else since
    if seq > events.last_seq():
        # The client saw a bus from before a restart; everything here is new to it.
        seq = 0
    # One thread per stream, outside the default pool the sync routes share.
    limiter = anyio.CapacityLimiter(1)
    yield f"retry: 3000\n: connected at seq {seq}\n\n"
    while True:
        if is_disconnected is not None and await is_disconnected():
            return
        batch = await anyio.to_thread.run_sync(
            events.wait, seq, interval, abandon_on_cancel=True, limiter=limiter
        )
        if not batch:
            if events.last_seq() < seq:  # the bus was cleared: start over with it
                seq = 0
            yield ": heartbeat\n\n"
            continue
        for event in batch:
            seq = event["seq"]
            yield _frame(event)


@router.get("/map/events")
async def map_events(
    request: Request,
    since: int | None = Query(default=None, description="Send events after this seq"),
    last_event_id: str | None = Header(default=None),
) -> StreamingResponse:
    return StreamingResponse(
        event_stream(start_seq(last_event_id, since), is_disconnected=request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# ---------------------------------------------------- the person's actions


class AnswerBody(BaseModel):
    answer: str = Field(min_length=1, max_length=80)
    remember: bool = False
    # {field: value} widens those amounts; a bare number is passed through for the
    # workstream service to apply to the workstream's one amount field.
    up_to: dict[str, Any] | float | None = None
    ignore: list[str] | None = None
    note: str = Field(default="", max_length=600)
    approval_token: str | None = None


class OverruleBody(BaseModel):
    reason: str = Field(min_length=1, max_length=600)
    approval_token: str | None = None


def _require_person(body_token: str | None, header_token: str | None) -> str:
    token = (body_token or header_token or "").strip()
    expected = config.get_settings().approval_token
    if not token or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(
            status_code=403, detail="this needs a person: missing or invalid approval token"
        )
    return token


def _service(name: str):
    try:
        from app.workstreams import service
    except ImportError:
        raise HTTPException(status_code=503, detail="the workstream service is not available") from None
    fn = getattr(service, name, None)
    if fn is None:
        raise HTTPException(status_code=503, detail=f"the workstream service has no '{name}'")
    return fn


def _as_http(exc: Exception) -> HTTPException:
    """The service's refusals, as the map's 404, 403 and 409 with the reason."""
    if isinstance(exc, HTTPException):
        return exc
    name = type(exc).__name__
    reason = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
    reason = str(reason) or name
    if isinstance(exc, LookupError) or name.endswith(("NotFound", "Unknown", "Missing")):
        return HTTPException(status_code=404, detail=reason)
    if isinstance(exc, PermissionError) or name in ("Forbidden", "ApprovalRequired"):
        return HTTPException(status_code=403, detail=reason)
    return HTTPException(status_code=409, detail=reason)


def _refusal(exc: Exception) -> bool:
    """Whether an exception is the service saying no, rather than a bug."""
    if isinstance(exc, (HTTPException, LookupError, ValueError, PermissionError)):
        return True
    module = type(exc).__module__ or ""
    return module.startswith(("app.workstreams", "app.policy", "app.harness"))


@router.post("/map/answer/{decision_id}")
def map_answer(
    decision_id: str, body: AnswerBody, x_approval_token: str | None = Header(default=None)
) -> Any:
    token = _require_person(body.approval_token, x_approval_token)
    answer = _service("answer")
    try:
        return answer(
            decision_id,
            body.answer,
            remember=body.remember,
            up_to=body.up_to,
            ignore=body.ignore,
            note=body.note,
            actor=OPERATOR,
            approval_token=token,
        )
    except Exception as exc:
        if not _refusal(exc):
            raise
        raise _as_http(exc) from exc


@router.post("/map/overrule/{precedent_id}")
def map_overrule(
    precedent_id: str, body: OverruleBody, x_approval_token: str | None = Header(default=None)
) -> Any:
    _require_person(body.approval_token, x_approval_token)
    overrule = _service("overrule")
    try:
        return overrule(precedent_id, reason=body.reason, actor=OPERATOR)
    except Exception as exc:
        if not _refusal(exc):
            raise
        raise _as_http(exc) from exc
