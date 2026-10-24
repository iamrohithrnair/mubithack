"""Put the demo in its opening state.

Fresh synthetic data, then the two precedents Dana already decided: one for
returns, one for AP invoices. Each goes through the real gate: a validated
candidate, a replay against the labeled history, and activation with the
approval token. Nothing here calls a model.

Usage (point it at a scratch database, never a real one):

    PRECEDENZ_DB_PATH=var/demo.db .venv/bin/python scripts/demo_setup.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, seed  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.domains import ap_invoices as ap  # noqa: E402
from app.orchestrator import activate_policy  # noqa: E402
from app.policy import store  # noqa: E402
from app.policy.replay import replay_candidate_policy  # noqa: E402

DANA = "dana.r@wharf-canary-optics.example"
TRACE = "trc_demo_setup"

RETURNS_PRECEDENT = {
    "family": "returns.missing_accessory",
    "name": "Replace one cheap missing accessory",
    "description": (
        "Replace one missing accessory up to $25 when it is not serialized, safety-critical "
        "or essential, the serial matches, there is no new damage and the evidence is complete."
    ),
    "conditions": [
        {"field": "missing_component_count", "operator": "eq", "value": 1},
        {"field": "missing_component_serialized", "operator": "eq", "value": False},
        {"field": "missing_component_safety_critical", "operator": "eq", "value": False},
        {"field": "missing_component_essential", "operator": "eq", "value": False},
        {"field": "serial_match", "operator": "eq", "value": True},
        {"field": "new_damage_present", "operator": "eq", "value": False},
        {"field": "evidence_complete", "operator": "eq", "value": True},
        {"field": "replacement_cost_usd", "operator": "lte", "value": 25.0},
    ],
    "actions": [
        {"type": "set_disposition", "disposition": "PARTS_HOLD"},
        {"type": "create_work_order", "work_order_type": "REPLACEMENT_PARTS", "max_cost_usd": 25.0},
        {"type": "close_exception", "resolution_code": "RESOLVED_PARTS_REPLACEMENT"},
    ],
    "min_confidence": 0.75,
}

AP_PRECEDENT = {
    "family": ap.FAMILY,
    "name": "Small clean price variance on a matched purchase order",
    "description": "Pay a variance up to $75 and 2% when the PO, receipt and tax all reconcile.",
    "conditions": [
        {"field": "variance_usd", "operator": "lte", "value": 75},
        {"field": "variance_pct", "operator": "lte", "value": 2},
        {"field": "po_match", "operator": "eq", "value": True},
        {"field": "receipt_match", "operator": "eq", "value": True},
        {"field": "tax_consistent", "operator": "eq", "value": True},
        {"field": "duplicate_suspected", "operator": "eq", "value": False},
        {"field": "vendor_on_hold", "operator": "eq", "value": False},
    ],
    "actions": ap.fixed_actions(75.0),
    "min_confidence": 0.75,
}


def decide_once(payload: dict, origin_case: str) -> str:
    """A person decides once: candidate, replay, activation with the approval token."""
    with db.session() as conn:
        record = store.create_candidate(
            conn, payload=payload, trace_id=TRACE, origin_case_id=origin_case, actor=DANA
        )
        report = replay_candidate_policy(conn, record.definition, enforce_coverage=False)
        if not report.passed:
            raise SystemExit(f"replay did not pass for {payload['name']}: {report.blocking_reasons}")
        store.attach_replay_report(conn, record.policy_id, report.to_dict(), trace_id=TRACE)
        activate_policy(
            conn, record.policy_id, approval_token=get_settings().approval_token, activated_by=DANA
        )
        return record.policy_id


def main() -> None:
    print(f"database: {db.describe()}")
    seed.seed(reset=True)
    returns_id = decide_once(RETURNS_PRECEDENT, "CASE-2001")
    ap_id = decide_once(AP_PRECEDENT, "AP-2001")
    print(f"returns precedent: {returns_id}")
    print(f"AP precedent:      {ap_id}")
    print("ready: start the server, open /map, then plant a workstream live")


if __name__ == "__main__":
    main()
