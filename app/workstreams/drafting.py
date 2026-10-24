"""Draft a precedent from one answer (docs/decide-once-plan.md, 5.3).

Deterministic, with no model. The precedent is exactly the case the person
decided, made wider only where the person says so:

  * flag and category fields: `eq` the observed value;
  * amount fields: `lte` the larger of the observed value and the person's `up_to`;
  * count fields: `eq` the observed value, or `lte` when the person gives `up_to`;
  * fields in `ignore` are dropped ("doesn't matter"); ignoring the amount that
    bounds the answer means "up to the workstream's ceiling", since a follow
    always carries a cap;
  * always-ask rules stay hard rules and never become part of a precedent;
  * actions: `spec.fixed_actions(cap)`, the repeatable answer and nothing else.
"""

from __future__ import annotations

from typing import Any

from app import domains
from app.policy.schema import PolicyDefinition, PolicyValidationError, validate_policy_payload
from app.workstreams import domain
from app.workstreams.contract import WorkstreamContract


class DraftError(ValueError):
    """The answer cannot be drafted as a precedent as given. Nothing is stored."""


def _number(value: float) -> int | float:
    return int(value) if float(value).is_integer() else float(value)


def check_options(
    contract: WorkstreamContract, up_to: dict[str, float], ignore: list[str]
) -> None:
    for name in [*up_to, *ignore]:
        if contract.field_named(name) is None:
            raise DraftError(f"'{name}' is not a field of {contract.label}")
    both = sorted(set(up_to) & set(ignore))
    if both:
        raise DraftError(f"a field is either widened or ignored, not both: {', '.join(both)}")
    for name, value in up_to.items():
        spec = contract.field_named(name)
        assert spec is not None
        if spec.kind not in ("amount", "count"):
            raise DraftError(f"up_to applies to amounts and counts; '{name}' is a {spec.kind}")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise DraftError(f"up_to for '{name}' must be a number of zero or more")
        if spec.kind == "count" and not float(value).is_integer():
            raise DraftError(f"up_to for '{name}' must be a whole number")
        if spec.ceiling is not None and float(value) > spec.ceiling:
            raise DraftError(
                f"up_to for '{name}' is {domain.show(spec, value)}, above the workstream's "
                f"ceiling {domain.show(spec, spec.ceiling)}"
            )


def draft_conditions(
    contract: WorkstreamContract,
    values: dict[str, Any],
    *,
    up_to: dict[str, float] | None = None,
    ignore: list[str] | None = None,
) -> tuple[list[dict[str, Any]], float | None]:
    """(conditions, cap). The cap is the bound on the amount field, if there is one."""
    up_to, ignore = dict(up_to or {}), list(ignore or [])
    check_options(contract, up_to, ignore)
    amount = contract.amount_field
    conditions: list[dict[str, Any]] = []
    cap: float | None = None

    for spec in contract.fields:
        name, seen = spec.name, values.get(spec.name)
        if name in ignore:
            if amount is not None and name == amount.name:
                cap = float(amount.ceiling)  # type: ignore[arg-type]
                conditions.append({"field": name, "operator": "lte", "value": _number(cap)})
            continue
        if spec.kind in ("flag", "category"):
            if seen is not None:
                conditions.append({"field": name, "operator": "eq", "value": seen})
        elif spec.kind == "amount":
            if seen is None and name not in up_to:
                continue
            bound = max(float(seen or 0.0), float(up_to.get(name, 0.0)))
            conditions.append({"field": name, "operator": "lte", "value": _number(round(bound, 2))})
            if amount is not None and name == amount.name:
                cap = round(bound, 2)
        else:  # count
            if name in up_to:
                bound = max(int(seen or 0), int(up_to[name]))
                conditions.append({"field": name, "operator": "lte", "value": bound})
            elif seen is not None:
                conditions.append({"field": name, "operator": "eq", "value": int(seen)})

    if not conditions:
        raise DraftError("nothing left to match: a precedent needs at least one field")
    if amount is not None:
        if cap is None:
            raise DraftError(f"{amount.name} is unknown on this case: give up_to for it, or ignore it")
        if cap <= 0:
            raise DraftError(f"{amount.name} is 0 on this case: give up_to for it")
    return conditions, cap


def draft(
    contract: WorkstreamContract,
    values: dict[str, Any],
    *,
    up_to: dict[str, float] | None = None,
    ignore: list[str] | None = None,
) -> tuple[dict[str, Any], PolicyDefinition]:
    """The candidate payload, validated, with its plain-English text as the description."""
    conditions, cap = draft_conditions(contract, values, up_to=up_to, ignore=ignore)
    spec = domains.get(contract.id)
    payload: dict[str, Any] = {
        "family": contract.id,
        "name": f"{contract.label}: {contract.repeatable_answer}"[:120],
        "description": "",
        "conditions": conditions,
        "actions": spec.fixed_actions(cap or 0.0),
    }
    try:
        definition = validate_policy_payload(payload)
    except PolicyValidationError as exc:
        raise DraftError("; ".join(exc.errors)) from exc
    payload["description"] = domain.precedent_text(contract, definition)[:600]
    return payload, validate_policy_payload(payload)
