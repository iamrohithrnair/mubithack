"""The shapes every workstream engine shares, and the engine registry.

An engine turns one decide request into an `Assessment`: follow or ask, with the
checks it ran and the reasons it stopped. Engines never execute anything. The
built-in workstreams (returns, AP invoices) and every planted workstream register
one engine each; the decision service looks it up by family.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from app.agent.providers import CallLog
    from app.db import Connection

FOLLOW = "follow"
ASK = "ask"
Verdict = Literal["follow", "ask"]


@dataclass(frozen=True)
class DecideRequest:
    """One case, as a caller sent it. Facts and text apply to planted workstreams only."""

    workstream: str
    case_ref: str
    idempotency_key: str
    facts: dict[str, Any] | None = None
    text: str | None = None
    source_revision: str | None = None
    caller: str = "api"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True)
class Assessment:
    verdict: Verdict
    reasons: tuple[str, ...] = ()
    checks: tuple[Check, ...] = ()
    precedent_id: str | None = None
    precedent_version: int | None = None
    actions: tuple[dict[str, Any], ...] = ()
    facts: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ask(cls, *reasons: str, checks: tuple[Check, ...] = (), **kw: Any) -> "Assessment":
        return cls(verdict=ASK, reasons=tuple(reasons), checks=checks, **kw)


class Engine(Protocol):
    family: str

    def assess(
        self,
        conn: "Connection",
        request: DecideRequest,
        *,
        trace_id: str,
        calls: "CallLog",
    ) -> Assessment: ...


_ENGINES: dict[str, Engine] = {}


def register_engine(engine: Engine) -> None:
    """Register or replace the engine for a family. Planted workstreams re-register on change."""
    _ENGINES[engine.family] = engine


def engine_for(family: str) -> Engine:
    try:
        return _ENGINES[family]
    except KeyError:
        raise KeyError(f"unknown workstream '{family}'") from None


def families() -> list[str]:
    return sorted(_ENGINES)
