"""Domain pack: returns, a kit back from a rental missing an accessory.

This is the domain the product was built around. Everything here was previously
hard-coded into the policy language, the replay gate, and the orchestrator; it is
gathered in one place so a second domain can exist beside it without touching any
of that machinery.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.db import Connection

from app.adapters import parts_catalog, returns_system, warehouse
from app.adapters.returns_system import AdapterError
from app.config import MAX_MISSING_COMPONENTS_CEILING, MAX_REPLACEMENT_COST_CEILING_USD
from app.domains import ActionSpec, DomainSpec, register
from app.facts import derive_facts
from app.policy.guardrails import evaluate_guardrails

FAMILY = "returns.missing_accessory"

BOOL_FIELDS = frozenset(
    {
        "missing_component_serialized",
        "missing_component_safety_critical",
        "missing_component_essential",
        "serial_match",
        "new_damage_present",
        "evidence_complete",
    }
)

NUMBER_FIELDS = frozenset({"missing_component_count", "replacement_cost_usd"})

ENUM_FIELDS = {"kit_category": frozenset({"camera_kit", "lens_kit", "audio_kit", "drone_kit"})}

# Outer walls. A proposal may be tighter than these. It may never be looser.
NUMERIC_CEILINGS = {
    "replacement_cost_usd": MAX_REPLACEMENT_COST_CEILING_USD,
    "missing_component_count": float(MAX_MISSING_COMPONENTS_CEILING),
}

ACTIONS = (
    ActionSpec(type="set_disposition", literals={"disposition": ("PARTS_HOLD",)}),
    ActionSpec(
        type="create_work_order",
        literals={"work_order_type": ("REPLACEMENT_PARTS",)},
        caps={"max_cost_usd": MAX_REPLACEMENT_COST_CEILING_USD},
        bound_by=("max_cost_usd", "replacement_cost_usd"),
    ),
    ActionSpec(type="close_exception", literals={"resolution_code": ("RESOLVED_PARTS_REPLACEMENT",)}),
)


def fixed_actions(cap: float) -> list[dict[str, Any]]:
    """The action set a proposal always gets. The agent never chooses these."""
    return [
        {"type": "set_disposition", "disposition": "PARTS_HOLD"},
        {
            "type": "create_work_order",
            "work_order_type": "REPLACEMENT_PARTS",
            "max_cost_usd": min(float(cap), MAX_REPLACEMENT_COST_CEILING_USD),
        },
        {"type": "close_exception", "resolution_code": "RESOLVED_PARTS_REPLACEMENT"},
    ]


def corpus(conn: "Connection") -> list[tuple[str, str, str]]:
    rows = conn.execute(
        """SELECT case_id, expected_label, scenario_note FROM return_cases
            WHERE is_historical = ? ORDER BY case_id""",
        (True,),
    ).fetchall()
    return [(r["case_id"], r["expected_label"], r["scenario_note"] or "") for r in rows]


def execute(conn: "Connection", action: Any, *, facts: Any, idem_key: str) -> dict[str, Any]:
    """Perform one business-system write. Raises AdapterError if it cannot.

    The orchestrator owns the loop, the audit records, and closing the exception;
    a domain owns only its own systems.
    """
    component = facts.missing_components[0] if facts.missing_components else None

    if action.type == "set_disposition":
        out = warehouse.set_disposition(
            conn, case_id=facts.case_id, disposition=action.disposition, idempotency_key=idem_key
        )
        return {
            "label": f"set_disposition:{action.disposition}",
            "detail": f"disposition set to {action.disposition}",
            "duplicate_suppressed": out["duplicate_suppressed"],
            "reference": facts.case_id,
        }

    if action.type == "create_work_order":
        if component is None:
            raise AdapterError("no missing component to raise a work order for")
        if facts.replacement_cost_usd is None:
            raise AdapterError("replacement cost unavailable at execution time")
        out = warehouse.create_work_order(
            conn,
            case_id=facts.case_id,
            work_order_type=action.work_order_type,
            component_id=component.component_id,
            cost_usd=facts.replacement_cost_usd,
            max_cost_usd=action.max_cost_usd,
            idempotency_key=idem_key,
        )
        return {
            "label": f"create_work_order:{action.work_order_type}",
            "detail": (
                f"{out['work_order_id']} for {component.component_id} at "
                f"${facts.replacement_cost_usd:.2f}"
            ),
            "duplicate_suppressed": out["duplicate_suppressed"],
            "reference": out["work_order_id"],
        }

    raise AdapterError(f"'{action.type}' is not an action this domain performs")


def verify(conn: "Connection", case_id: str) -> tuple[list[str], list[str]]:
    """Read the business systems back. Returns (checks passed, failures)."""
    checks: list[str] = []
    failures: list[str] = []

    disposition = warehouse.get_disposition(conn, case_id)
    if disposition and disposition["disposition"] == "PARTS_HOLD":
        checks.append(f"disposition is {disposition['disposition']}")
    else:
        failures.append("disposition was not written as PARTS_HOLD")

    work_orders = warehouse.get_work_orders(conn, case_id)
    if len(work_orders) == 1:
        checks.append(f"one work order on file: {work_orders[0]['work_order_id']}")
    elif not work_orders:
        failures.append("no replacement-parts work order was created")
    else:
        failures.append(f"{len(work_orders)} work orders exist for this case; expected exactly one")

    return checks, failures


# ------------------------------------------------- semantic check renderings

_CONDITION_PROSE = {
    ("serial_match", True): "the serial number read from the returned unit matches the serial "
    "number expected on the order",
    ("evidence_complete", True): "every required inspection evidence item is present (exterior "
    "photo, serial photo, component checklist, inspector id, weight check)",
    ("new_damage_present", False): "no new damage is present on the returned item",
    ("missing_component_serialized", False): "the missing component is not serialized",
    ("missing_component_safety_critical", False): "the missing component is not safety-critical",
    ("missing_component_essential", False): "the missing component is not essential",
}


def _condition_prose(condition: Any) -> str:
    field, op, value = condition.field, condition.operator.value, condition.value
    if (field, value) in _CONDITION_PROSE and op == "eq":
        return _CONDITION_PROSE[(field, value)]
    if field == "missing_component_count" and op == "eq":
        return f"exactly {int(value)} component{'s are' if int(value) != 1 else ' is'} missing"
    if field == "missing_component_count" and op == "lte":
        return f"at most {int(value)} component{'s are' if int(value) != 1 else ' is'} missing"
    if field == "replacement_cost_usd" and op in ("lte", "lt"):
        bound = "at most" if op == "lte" else "under"
        return f"the replacement cost is known and {bound} ${float(value):.2f}"
    if field == "kit_category" and op == "in":
        return f"the kit category is one of {', '.join(value)}"
    return condition.describe()


def policy_text(definition: Any) -> str:
    """The matched policy as a person would write it."""
    conditions = "; ".join(_condition_prose(c) for c in definition.conditions)
    return (
        f"Return exception policy \"{definition.name}\". A return case may be resolved "
        "automatically, with a replacement part ordered and the kit restocked, only when ALL of "
        f"these hold: {conditions}. If any condition fails or cannot be established, the case "
        "must be escalated to a person."
    )


def evidence_text(conn: "Connection", case_id: str) -> str:
    """The case as the source systems hold it, free-text notes included.

    Raw values only: serials are shown side by side, not compared, so the check
    reads the record independently of the fact derivation it is checking.
    """
    case = returns_system.get_return_case(conn, case_id)
    bom = returns_system.get_expected_components(conn, case["sku"])
    received = set(case["received_components"])
    lines = [
        f"Returns system record for case {case['case_id']}, order {case['order_id']}: "
        f"{case['kit_name']} ({case['sku']}, {case['kit_category']}).",
        f"Serial expected on the order: {case['expected_serial']}. "
        f"Serial read from the returned unit: {case['received_serial'] or 'none recorded'}.",
    ]
    evidence = case["inspection_evidence"]
    for name in returns_system.REQUIRED_EVIDENCE_FIELDS:
        value = evidence.get(name)
        lines.append(f"Inspection evidence {name}: {value if value not in (None, '') else 'MISSING'}.")
    lines.append(
        "New damage recorded on the inspection form: "
        + ("yes." if case["new_damage_present"] else "no.")
    )
    missing = [c for c in bom if c["component_id"] not in received]
    if not missing:
        lines.append("Every component on the bill of materials was received.")
    for component in missing:
        try:
            priced = parts_catalog.lookup_replacement_cost(conn, component["component_id"])
            cost = (
                f"${priced['replacement_cost_usd']:.2f}"
                if priced["replacement_cost_usd"] is not None
                else "not in the parts catalog (cost unknown)"
            )
        except AdapterError as exc:
            cost = f"unavailable: {exc}"
        yes_no = lambda flag: "yes" if flag else "no"  # noqa: E731
        lines.append(
            f"Missing component: {component['name']} ({component['component_id']}); "
            f"serialized: {yes_no(component['serialized'])}; "
            f"safety-critical: {yes_no(component['safety_critical'])}; "
            f"essential: {yes_no(component['essential'])}; replacement cost {cost}."
        )
    notes = (case["inspector_notes"] or "").strip()
    lines.append(f"Inspector notes (free text): {notes if notes else 'none'}")
    return "\n".join(lines)


SPEC = register(
    DomainSpec(
        family=FAMILY,
        label="Returns — missing accessory",
        subject="return case",
        bool_fields=BOOL_FIELDS,
        number_fields=NUMBER_FIELDS,
        enum_fields=ENUM_FIELDS,
        numeric_ceilings=NUMERIC_CEILINGS,
        actions=ACTIONS,
        required_action_types=("set_disposition", "create_work_order", "close_exception"),
        derive_facts=derive_facts,
        guardrails=evaluate_guardrails,
        corpus=corpus,
        execute=execute,
        verify=verify,
        fixed_actions=fixed_actions,
        policy_text=policy_text,
        evidence_text=evidence_text,
    )
)
