"""Precedenz client: a small HTTP client, the `precedenz` CLI, and the MCP server.

It talks to a Precedenz server over HTTP only and never imports the app.
Owner: agent E.
"""

from precedenz_client.client import (
    ApprovalRequired,
    AuthError,
    Conflict,
    InvalidRequest,
    NotFound,
    Precedenz,
    PrecedenzError,
    ServerError,
    Unreachable,
)

__all__ = [
    "ApprovalRequired",
    "AuthError",
    "Conflict",
    "InvalidRequest",
    "NotFound",
    "Precedenz",
    "PrecedenzError",
    "ServerError",
    "Unreachable",
]
