"""Planted workstreams: parse a contract, register it, and load what is stored.

Registering a workstream puts two things in process memory: a `DomainSpec` in
`app.domains` (so the policy language, replay and the activation gate govern
it like any built-in domain) and a `PlantedEngine` in `app.harness.engines` (so
the decision service can assess its cases). The database is the record; memory
is rebuilt from it by `load_all()` at startup.
"""

from __future__ import annotations

import json
import logging
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from pydantic import ValidationError

from app import db, domains
from app.harness import engines
from app.workstreams.contract import WorkstreamContract
from app.workstreams.domain import build_spec
from app.workstreams.engine import PlantedEngine

log = logging.getLogger(__name__)

_lock = threading.RLock()


class ContractError(ValueError):
    """A contract that cannot be planted, with every reason."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or [message]


def _errors(exc: ValidationError) -> list[str]:
    out = []
    for error in exc.errors():
        where = ".".join(str(p) for p in error["loc"])
        out.append(f"{where}: {error['msg']}" if where else error["msg"])
    return out


def _shape(spec: domains.DomainSpec, name: str) -> tuple[Any, ...] | None:
    """How the policy language sees one field: its type, and its ceiling or values."""
    if name in spec.bool_fields:
        return ("flag",)
    if name in spec.number_fields:
        return ("number", spec.numeric_ceilings.get(name))
    if name in spec.enum_fields:
        return ("category", frozenset(spec.enum_fields[name]))
    return None


def _check_collisions(spec: domains.DomainSpec) -> list[str]:
    """Field names are global to the policy language: a condition is validated
    against whichever registered domain lists the field. A shared name is fine
    only when every domain that uses it gives it the same type and bounds."""
    domains.load()
    problems = []
    for other in domains.all_specs():
        if other.family == spec.family:
            continue
        for name in sorted(spec.allowed_fields & other.allowed_fields):
            if _shape(spec, name) != _shape(other, name):
                problems.append(
                    f"field '{name}' is already used by '{other.family}' with a different type, "
                    "ceiling or values; rename it or match that definition"
                )
    return problems


@contextmanager
def _provisionally(spec: domains.DomainSpec) -> Iterator[None]:
    """Register a spec for the length of a validation, then put back what was there."""
    with _lock:
        previous = domains.upsert(spec)
        try:
            yield
        finally:
            if previous is None:
                domains.unregister(spec.family)
            else:
                domains.upsert(previous)


def parse_contract(payload: dict[str, Any] | WorkstreamContract) -> WorkstreamContract:
    """Validate an untrusted contract. Raises ContractError with every problem.

    Always-ask rules use the policy language's own `Condition`, which only
    accepts fields a registered domain allowlists. So the contract is validated
    without them first, its domain registered provisionally, and the rules
    validated against it; nothing stays registered.
    """
    if isinstance(payload, WorkstreamContract):
        payload = json.loads(payload.model_dump_json())
    if not isinstance(payload, dict):
        raise ContractError("a contract is a JSON object")
    data = dict(payload)
    rules = data.pop("always_ask", None) or []
    try:
        base = WorkstreamContract.model_validate({**data, "always_ask": []})
    except ValidationError as exc:
        raise ContractError("the contract is not valid", _errors(exc)) from exc

    spec = build_spec(base)
    collisions = _check_collisions(spec)
    if collisions:
        raise ContractError("the contract's fields clash with another workstream", collisions)

    with _provisionally(spec):
        try:
            contract = WorkstreamContract.model_validate({**data, "always_ask": rules})
        except ValidationError as exc:
            raise ContractError("the contract's always-ask rules are not valid", _errors(exc)) from exc
        except KeyError as exc:  # raised by the policy language for an unknown family
            raise ContractError(str(exc)) from exc
    unknown = [r.field for r in contract.always_ask if contract.field_named(r.field) is None]
    if unknown:
        raise ContractError(
            "always-ask rules may only test this workstream's own fields",
            [f"always_ask: '{name}' is not a field of '{contract.id}'" for name in unknown],
        )
    return contract


def register(contract: WorkstreamContract) -> PlantedEngine:
    """Put a parsed contract's domain and engine in process memory (replacing any)."""
    with _lock:
        domains.load()
        domains.upsert(build_spec(contract))
        engine = PlantedEngine(contract)
        engines.register_engine(engine)
        return engine


def contract_from_row(row: dict[str, Any]) -> WorkstreamContract:
    return parse_contract(json.loads(row["contract"]))


def load_all() -> list[str]:
    """Register every stored workstream. Returns the ids registered.

    A stored contract that no longer validates is skipped and logged rather than
    stopping every other workstream from loading.
    """
    with db.read_only() as conn:
        rows = conn.execute("SELECT id, contract FROM workstreams ORDER BY planted_at, id").fetchall()
    loaded: list[str] = []
    for row in rows:
        try:
            register(contract_from_row(row))
        except ContractError as exc:
            log.warning("workstream %s not loaded: %s", row["id"], "; ".join(exc.errors))
            continue
        loaded.append(row["id"])
    return loaded


def unregister(family: str) -> None:
    """Forget a planted workstream in this process. Tests use it to stay isolated."""
    with _lock:
        domains.unregister(family)
        engines._ENGINES.pop(family, None)


def forget_planted() -> list[str]:
    """Forget every planted workstream in this process, e.g. after a reset wiped
    the tables. Built-in domains and engines stay. Returns the ids forgotten."""
    from app.workstreams.contract import BUILT_IN_IDS

    with _lock:
        planted = sorted(
            {f for f, e in engines._ENGINES.items() if isinstance(e, PlantedEngine)}
            | (set(domains.families()) - BUILT_IN_IDS)
        )
        for family in planted:
            unregister(family)
        return planted
