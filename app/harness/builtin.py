"""Engines for the built-in workstreams: returns and AP invoices.

Both re-derive every fact from their own source system by `case_ref`; neither
reads `facts` or `text` from the caller. Neither acts: no domain `execute` or
`verify`, no adapter write, no exception row. A follow is a statement of what
the precedent says to do, for the caller's own system to carry out.

  returns: the inbox's own assessment (`orchestrator.assess_case`): the agent
           reads the case through the gateway, then reconciliation, guardrails,
           the precedent match, its minimum confidence, and Banyan Sapling.
  AP:      rules only, with no model call: ledger facts, AP guardrails, the
           precedent match, and Sapling where the domain renders text for it.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.providers import CallLog
    from app.db import Connection

from app import orchestrator
from app.adapters.returns_system import AdapterError
from app.harness.engines import FOLLOW, Assessment, Check, DecideRequest, register_engine
from app.policy import store as policy_store
from app.policy.engine import select_matching_policies
from app.policy.schema import spec_for

RETURNS_FAMILY = "returns.missing_accessory"
AP_FAMILY = "ap.invoice_variance"


class ReturnsEngine:
    family = RETURNS_FAMILY

    def assess(
        self, conn: "Connection", request: DecideRequest, *, trace_id: str, calls: "CallLog"
    ) -> Assessment:
        known = conn.execute(
            "SELECT 1 FROM return_cases WHERE case_id = ?", (request.case_ref,)
        ).fetchone()
        if known is None:
            reason = (
                f"unknown case '{request.case_ref}': no such return case in the returns system"
            )
            return Assessment.ask(
                reason, checks=(Check("case is known to the returns system", False, reason),)
            )

        assessment = orchestrator.assess_case(
            conn, request.case_ref, trace_id=trace_id, call_log=calls
        )
        return dataclasses.replace(
            assessment,
            checks=(Check("case is known to the returns system", True), *assessment.checks),
        )


class InvoiceVarianceEngine:
    family = AP_FAMILY

    def assess(
        self, conn: "Connection", request: DecideRequest, *, trace_id: str, calls: "CallLog"
    ) -> Assessment:
        spec = spec_for(AP_FAMILY)
        checks: list[Check] = []

        def ask(*reasons: str, precedent: policy_store.PolicyRecord | None = None) -> Assessment:
            return Assessment.ask(
                *reasons,
                checks=tuple(checks),
                precedent_id=precedent.policy_id if precedent else None,
                precedent_version=precedent.version if precedent else None,
                facts=view,
            )

        view: dict[str, object] = {}
        try:
            facts = spec.derive_facts(conn, request.case_ref)
        except AdapterError as exc:
            checks.append(Check("facts re-derived from the AP ledger", False, str(exc)))
            return ask(f"could not derive invoice facts: {exc}")
        view = dict(facts.policy_view())
        checks.append(Check("facts re-derived from the AP ledger", True))

        active_records = [p for p in policy_store.list_active(conn) if p.family == AP_FAMILY]
        active = [(p.policy_id, p.definition) for p in active_records]
        matches = select_matching_policies(active, facts)
        nearest = active_records[-1] if active_records else None

        guard = spec.guardrails(
            facts, tool_failures=tuple(facts.tool_failures), matching_policy_count=len(matches)
        )
        if guard.blocked:
            checks.append(Check("hard guardrails", False, "; ".join(guard.reasons)))
            return ask(*guard.reasons, precedent=nearest)
        checks.append(Check("hard guardrails", True))

        if not matches:
            if not active:
                checks.append(
                    Check("matches a precedent", False, "no active precedent in this workstream")
                )
                return ask(orchestrator.NO_PRECEDENT_REASON)
            near = orchestrator._near_miss_reasons(active, facts)
            checks.append(Check("matches a precedent", False, "; ".join(near)))
            return ask(orchestrator.OUTSIDE_PRECEDENT_REASON, *near, precedent=nearest)

        policy_id, definition, _match = matches[0]
        record = policy_store.get(conn, policy_id)
        checks.append(Check(f"matches precedent v{record.version}", True))

        # No model reads an invoice, so there is no confidence to hold to the
        # precedent's minimum. Sapling runs only if the domain renders text for it.
        blocked_by, detail = orchestrator.semantic_verdict(
            conn,
            request.case_ref,
            definition,
            trace_id=trace_id,
            exception_id=None,
            policy_id=policy_id,
        )
        checks.append(Check(orchestrator.SAPLING_CHECK, blocked_by is None, detail))
        if blocked_by is not None:
            return ask(blocked_by, precedent=record)

        return Assessment(
            verdict=FOLLOW,
            reasons=(),
            checks=tuple(checks),
            precedent_id=policy_id,
            precedent_version=record.version,
            actions=tuple(a.model_dump() for a in definition.actions),
            facts=view,
        )


def register() -> None:
    """Register the built-in engines with app.harness.engines."""
    register_engine(ReturnsEngine())
    register_engine(InvoiceVarianceEngine())
