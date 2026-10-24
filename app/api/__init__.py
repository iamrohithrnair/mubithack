"""Machine-facing routes. `/v1` is the decision API; `/map` serves the live map.

Two credentials, never interchangeable. A caller (pipeline, CLI, MCP server)
sends the API token. Anything only a person may do (plant a workstream, remember
an answer as a precedent, overrule one) also needs the approval token, which the
MCP server never carries.
"""

from __future__ import annotations

from fastapi import Header, HTTPException

from app import config


def require_api_token(authorization: str | None = Header(default=None)) -> str:
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not token or token != config.get_settings().api_token:
        raise HTTPException(status_code=401, detail="missing or invalid API token")
    return token


def require_approval_token(x_approval_token: str | None = Header(default=None)) -> str:
    if not x_approval_token or x_approval_token != config.get_settings().approval_token:
        raise HTTPException(
            status_code=403, detail="this needs a person: missing or invalid approval token"
        )
    return x_approval_token


def caller_label(x_precedenz_caller: str | None = Header(default=None)) -> str:
    """Informational only: which kind of caller sent the request (api, cli, agent, script)."""
    return (x_precedenz_caller or "api").strip()[:40] or "api"
