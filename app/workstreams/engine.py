"""The engine for a planted workstream: follow a precedent, or ask a person.

    facts (caller, reader, or both, reconciled)  ->  hard rules  ->  precedent?
      -> matches, and Sapling confirms the text when the contract asks for it: FOLLOW
      -> anything else, including any failure: ASK, with the reason

A request with neither facts nor text reuses the facts already on file. That is
how a waiting ask is assessed again after a person decides once: no new model
call, and the same rules. Nothing here executes anything; a follow is an intent
the caller carries out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.db import Connection

from app import audit, config, sapling
from app.audit import AuditEventType
from app.harness.engines import FOLLOW, Assessment, Check, DecideRequest
from app.policy import store as policy_store
from app.policy.engine import evaluate_condition, match_policy
from app.policy.schema import Operator
from app.workstreams import cases, domain, reader
from app.workstreams.contract import WorkstreamContract

NO_PRECEDENT = "no precedent yet: a person decides once"


class PlantedEngine:
    """One per planted workstream; its family is the contract id."""

    def __init__(self, contract: WorkstreamContract) -> None:
        self.contract = contract
        self.family = contract.id

    # ------------------------------------------------------------- assess

    def assess(
        self, conn: "Connection", request: DecideRequest, *, trace_id: str, calls: Any
    ) -> Assessment:
        checks: list[Check] = []
        got = self._facts(conn, request, trace_id=trace_id, calls=calls, checks=checks)
        if isinstance(got, Assessment):
            return got
        facts = got
        values = dict(facts.values)

        guard, reasons = domain.guard_checks(self.contract, facts)
        checks.extend(guard)
        if reasons:
            return Assessment.ask(*reasons, checks=tuple(checks), facts=values)

        record = policy_store.active_for(conn, self.family)
        if record is None:
            checks.append(Check("matches a precedent", False, "no precedent yet"))
            return Assessment.ask(NO_PRECEDENT, checks=tuple(checks), facts=values)

        name = f"matches precedent v{record.version}"
        if not match_policy(record.definition, facts).matched:
            differs = self.differences(record.definition, facts)
            checks.append(Check(name, False, "; ".join(differs)))
            return Assessment.ask(
                *differs,
                checks=tuple(checks),
                precedent_id=record.policy_id,
                precedent_version=record.version,
                facts=values,
            )
        checks.append(Check(name, True, record.definition.description))

        sapling_check, sapling_reason = self._semantic_check(
            conn, record, request.case_ref, trace_id=trace_id
        )
        if sapling_check is not None:
            checks.append(sapling_check)
        if sapling_reason:
            return Assessment.ask(
                sapling_reason,
                checks=tuple(checks),
                precedent_id=record.policy_id,
                precedent_version=record.version,
                facts=values,
            )

        return Assessment(
            verdict=FOLLOW,
            reasons=(),
            checks=tuple(checks),
            precedent_id=record.policy_id,
            precedent_version=record.version,
            # From the precedent and the ActionSpec caps it was validated under;
            # never from anything a model said.
            actions=tuple({"type": a.type, **a.params()} for a in record.definition.actions),
            facts=values,
        )

    # -------------------------------------------------------------- facts

    def _facts(
        self,
        conn: "Connection",
        request: DecideRequest,
        *,
        trace_id: str,
        calls: Any,
        checks: list[Check],
    ) -> cases.PlantedFacts | Assessment:
        contract, case_ref = self.contract, request.case_ref
        text = (request.text or "").strip() or None

        if request.facts is None and text is None:
            stored = cases.load(conn, self.family, case_ref)
            if stored is None:
                return Assessment.ask(
                    f"no facts or text for {contract.subject} {case_ref}: send them with the case"
                )
            checks.append(
                Check(
                    "facts on file",
                    stored.source != cases.SOURCE_UNCONFIRMED,
                    f"source {stored.source}, received {stored.received_at}",
                )
            )
            return cases.facts_for(conn, contract, case_ref)

        caller, problems, ignored = cases.normalize(contract, request.facts)

        def stop(*reasons: str) -> Assessment:
            cases.save(
                conn, self.family, case_ref, facts=caller, text=text,
                source=cases.SOURCE_UNCONFIRMED, revision=request.source_revision,
            )
            return Assessment.ask(*reasons, checks=tuple(checks), facts=cases.full_view(contract, caller))

        if problems:
            checks.append(Check("facts are well formed", False, "; ".join(problems)))
            return stop(*problems)
        if ignored:
            checks.append(Check("facts are well formed", True, f"ignored unknown fields: {', '.join(ignored)}"))

        source = cases.SOURCE_CALLER
        merged = dict(caller)
        if contract.reader and text:
            try:
                reading = reader.read(
                    conn, contract, case_ref=case_ref, text=text, trace_id=trace_id, calls=calls
                )
            except reader.ReaderError as exc:
                checks.append(Check(f"read the {contract.subject}", False, str(exc)))
                return stop(f"could not read the {contract.subject}: {exc}")
            read_values, read_problems, _ = cases.normalize(contract, reading)
            if read_problems:
                detail = "; ".join(read_problems)
                checks.append(Check(f"read the {contract.subject}", False, detail))
                return stop(f"could not read the {contract.subject}: {detail}")
            checks.append(
                Check(f"read the {contract.subject}", True, f"{len(read_values)} of {len(contract.fields)} fields stated")
            )
            disagreements = [
                f"the {contract.subject} text says {spec.name} is {domain.show(spec, read_values[spec.name])}, "
                f"but the caller sent {domain.show(spec, caller[spec.name])}"
                for spec in contract.fields
                if spec.name in caller
                and spec.name in read_values
                and not cases.same(spec, caller[spec.name], read_values[spec.name])
            ]
            if caller:
                checks.append(
                    Check(
                        "caller facts match the text",
                        not disagreements,
                        "; ".join(disagreements) or f"{len(caller)} fields compared",
                    )
                )
            if disagreements:
                return stop(*disagreements)
            merged = {**read_values, **caller}
            source = cases.SOURCE_BOTH if caller else cases.SOURCE_READER

        cases.save(
            conn, self.family, case_ref, facts=merged, text=text,
            source=source, revision=request.source_revision,
        )
        return cases.PlantedFacts(
            case_id=case_ref, values=cases.full_view(contract, merged), text=text, source=source
        )

    # --------------------------------------------------------- near misses

    def differences(self, definition: Any, facts: cases.PlantedFacts) -> list[str]:
        """What keeps this case outside the precedent, one reason per condition."""
        view = facts.policy_view()
        out: list[str] = []
        for condition in definition.conditions:
            if evaluate_condition(condition, view).passed:
                continue
            spec = self.contract.field_named(condition.field)
            name, op = condition.field, condition.operator
            seen, bound = domain.show(spec, view.get(name)), domain.show(spec, condition.value)
            if view.get(name) is None:
                out.append(f"{name} is unknown; the precedent needs {domain.describe(self.contract, condition)}")
            elif op == Operator.LTE:
                out.append(f"{name} {seen} is above the precedent's {bound}")
            elif op == Operator.LT:
                out.append(f"{name} {seen} is not below the precedent's {bound}")
            elif op == Operator.GTE:
                out.append(f"{name} {seen} is below the precedent's {bound}")
            elif op == Operator.GT:
                out.append(f"{name} {seen} is not above the precedent's {bound}")
            elif op == Operator.EQ:
                out.append(f"{name} is {seen}, not the precedent's {bound}")
            elif op == Operator.IN:
                out.append(f"{name} is {seen}, not one of the precedent's {bound}")
            else:
                out.append(f"{name} is {seen}, which the precedent excludes")
        return out or ["the precedent does not cover this case"]

    # ------------------------------------------------------------ Sapling

    def _semantic_check(
        self, conn: "Connection", record: Any, case_ref: str, *, trace_id: str
    ) -> tuple[Check | None, str | None]:
        """Banyan Sapling reads the case's own text against the precedent.

        Only for contracts with `evidence: "text"`. It can stop a follow, never
        start one: no text, an outage, or anything short of a confident
        `supported` asks.
        """
        if self.contract.evidence != "text":
            return None, None
        cfg = config.get_settings()
        if not cfg.sapling_enforced:
            return Check("Banyan Sapling: not run", True, "semantic checks are off in this environment"), None

        name = "Banyan Sapling: evidence supports it"
        try:
            evidence = domain.make_evidence_text(self.contract)(conn, case_ref)
        except LookupError as exc:
            return Check(name, False, str(exc)), f"Banyan Sapling could not check it: {exc}"

        policy = domain.make_policy_text(self.contract)(record.definition)
        verdict = sapling.verify(policy, evidence, settings=cfg)
        passed = verdict.passed(cfg.sapling_min_support)
        if passed:
            check, reason = Check(name, True, f"supported, p={verdict.support:.2f}"), None
        elif verdict.error is not None:
            reason = f"Banyan Sapling could not be reached ({verdict.error}), so it could not check the evidence"
            check = Check(name, False, reason)
        elif verdict.raw_label == sapling.SUPPORTED:
            reason = (
                f"Banyan Sapling supports it only weakly (p={verdict.support:.2f}, "
                f"needs {cfg.sapling_min_support:.2f})"
            )
            check = Check(name, False, reason)
        else:
            label = (verdict.raw_label or "unknown").replace("_", " ")
            reason = f"Banyan Sapling could not confirm the evidence ({label})"
            check = Check(name, False, f"{label}, p={verdict.probabilities.get(verdict.raw_label or '', 0.0):.2f}")

        audit.record(
            conn,
            trace_id=trace_id,
            event_type=AuditEventType.SEMANTIC_SUPPORTED if passed else AuditEventType.SEMANTIC_BLOCKED,
            actor="sapling",
            case_id=case_ref,
            policy_id=record.policy_id,
            payload={
                **verdict.payload(),
                "workstream": self.family,
                "min_support": cfg.sapling_min_support,
                **({"reasons": [reason]} if reason else {}),
            },
        )
        return check, reason
