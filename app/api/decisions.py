"""/v1 decision routes: decide, receipts, open questions, outcomes. Owner: agent A."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/v1", tags=["decisions"])
