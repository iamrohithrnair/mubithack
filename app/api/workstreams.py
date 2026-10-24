"""/v1 workstream routes: plant, templates, answer, overrule. Owner: agent C."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/v1", tags=["workstreams"])
