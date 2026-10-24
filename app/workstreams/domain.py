"""A planted workstream as a `DomainSpec`: the same governance machinery, from data.

The policy language, replay, and the activation gate know nothing about meal
claims or meeting invites. A contract supplies what a domain pack would: the
allowlisted fields and their ceilings, one action ("answer") whose value is
pinned to the repeatable answer and whose cap is bound by the amount, the stored
facts, the past answers to replay against, and hard rules (required fields,
ceilings, always-ask) that no precedent can relax.

`execute` and `verify` raise: the caller's own system carries out a follow.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from app.db import Connection

from app.domains import ActionSpec, DomainSpec
from app.harness.engines import Check
from app.policy.engine import evaluate_condition
from app.policy.guardrails import GuardrailResult
from app.policy.replay import LABEL_AUTO, LABEL_ESCALATE
from app.policy.schema import Condition, Operator
from app.workstreams import cases
from app.workstreams.contract import FieldSpec, WorkstreamContract

ANSWER_ACTION = "answer"
HISTORY_PREFIX = "history"


def history_decision_id(workstream: str, case_ref: str) -> str:
    """The `decision_id` a template's past answer is filed under.

    Past answers predate Precedenz, so no decision exists for them; they live in
    `harness_answers` under this id, and in `harness_cases` with source
    "history".
    """
    return f"{HISTORY_PREFIX}:{workstream}:{case_ref}"


def cap_param(contract: WorkstreamContract) -> str | None:
    amount = contract.amount_field
    return f"max_{amount.name}" if amount else None


# ------------------------------------------------------------- formatting


def show(spec: FieldSpec | None, value: Any) -> str:
    """A value as a reason states it: 140.00, true, crm."""
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "true" if value else "false"
    if spec is not None and spec.kind == "amount":
        return f"{float(value):.2f}"
    if spec is not None and spec.kind == "count":
        return str(int(value))
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _money(spec: FieldSpec, value: float) -> str:
    if spec.name.endswith("_usd") or "usd" in spec.label.lower() or "$" in spec.label:
        return f"${float(value):,.2f}"
    return f"{float(value):,.2f}"


_SYMBOL = {
    Operator.EQ: "is",
    Operator.NEQ: "is not",
    Operator.LT: "<",
    Operator.LTE: "<=",
    Operator.GT: ">",
    Operator.GTE: ">=",
    Operator.IN: "is one of",
    Operator.NOT_IN: "is not one of",
}


def describe(contract: WorkstreamContract, condition: Condition) -> str:
    """`amount_usd > 150.00`, `alcohol_only is true`: the rule, as a reason names it."""
    spec = contract.field_named(condition.field)
    return f"{condition.field} {_SYMBOL[condition.operator]} {show(spec, condition.value)}"


def _join(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def phrase(contract: WorkstreamContract, condition: Condition) -> str:
    """One precedent condition in plain English: "a receipt is attached"."""
    spec = contract.field_named(condition.field)
    if spec is None:
        return describe(contract, condition)
    label = spec.label[0].lower() + spec.label[1:]
    op, value = condition.operator, condition.value
    if spec.kind == "flag" and op in (Operator.EQ, Operator.NEQ):
        truth = bool(value) if op == Operator.EQ else not bool(value)
        if truth:
            return spec.when_true or label
        return spec.when_false or f"not {label}"
    if spec.kind == "category":
        if op == Operator.EQ:
            return f"the {label} is {value}"
        if op == Operator.NEQ:
            return f"the {label} is not {value}"
        listed = " or ".join(value) if isinstance(value, list) else str(value)
        return f"the {label} is {listed}" if op == Operator.IN else f"the {label} is not {listed}"
    number = _money(spec, value) if spec.kind == "amount" else show(spec, value)
    words = {
        Operator.EQ: "is exactly",
        Operator.NEQ: "is not",
        Operator.LT: "is under",
        Operator.LTE: "is at most",
        Operator.GT: "is over",
        Operator.GTE: "is at least",
    }.get(op, _SYMBOL[op])
    return f"the {label} {words} {number}"


def plural(subject: str) -> str:
    if subject.endswith(("s", "x", "ch", "sh")):
        return f"{subject}es"
    if subject.endswith("y") and subject[-2:-1] not in "aeiou":
        return f"{subject[:-1]}ies"
    return f"{subject}s"


def precedent_text(contract: WorkstreamContract, definition: Any) -> str:
    """The precedent as the person would say it.

    "Approve meal claims up to $75.00 when a receipt is attached and it is not
    alcohol only."
    """
    amount = contract.amount_field
    verb = contract.repeatable_answer[:1].upper() + contract.repeatable_answer[1:]
    lead = f"{verb} {plural(contract.subject)}"
    rest: list[str] = []
    for condition in definition.conditions:
        if (
            amount is not None
            and condition.field == amount.name
            and condition.operator in (Operator.LTE, Operator.LT)
        ):
            word = "up to" if condition.operator == Operator.LTE else "under"
            lead += f" {word} {_money(amount, condition.value)}"
            continue
        rest.append(phrase(contract, condition))
    return f"{lead} when {_join(rest)}." if rest else f"{lead}."


# ---------------------------------------------------------------- guards


def guard_checks(
    contract: WorkstreamContract, facts: cases.PlantedFacts
) -> tuple[list[Check], list[str]]:
    """The hard rules, as receipt checks plus the reasons any of them failed.

    Required fields present, every amount and count inside its ceiling, and no
    always-ask rule holding. An always-ask rule on an unknown field cannot be
    checked, so it asks too. No precedent can relax any of these.
    """
    view = facts.policy_view()
    checks: list[Check] = []
    reasons: list[str] = []

    if not facts.confirmed:
        reasons.append("the facts on file were never confirmed; send the case again")
        checks.append(Check("facts confirmed", False, "the reader failed or disagreed"))

    missing = [f.name for f in contract.fields if f.required and view.get(f.name) is None]
    checks.append(
        Check(
            "required fields present",
            not missing,
            f"missing: {', '.join(missing)}" if missing else f"{len(contract.fields)} fields",
        )
    )
    reasons.extend(f"required field missing: {name}" for name in missing)

    over: list[str] = []
    for spec in contract.fields:
        value = view.get(spec.name)
        if spec.ceiling is not None and value is not None and float(value) > spec.ceiling:
            over.append(f"{spec.name} {show(spec, value)} is above the ceiling {show(spec, spec.ceiling)}")
    if any(f.ceiling is not None for f in contract.fields):
        checks.append(Check("within ceilings", not over, "; ".join(over)))
    reasons.extend(over)

    hits: list[str] = []
    for rule in contract.always_ask:
        if view.get(rule.field) is None:
            hits.append(f"cannot check the always-ask rule {describe(contract, rule)}: {rule.field} is unknown")
        elif evaluate_condition(rule, view).passed:
            hits.append(f"always ask: {describe(contract, rule)}")
    checks.append(
        Check(
            "always-ask rules",
            not hits,
            "; ".join(hits) if hits else f"{len(contract.always_ask)} rule(s), none apply",
        )
    )
    reasons.extend(hits)
    return checks, reasons


def make_guardrails(contract: WorkstreamContract) -> Callable[..., GuardrailResult]:
    def guardrails(
        facts: cases.PlantedFacts,
        *,
        confidence: float | None = None,
        tool_failures: tuple[str, ...] = (),
        matching_policy_count: int = 0,
    ) -> GuardrailResult:
        _, reasons = guard_checks(contract, facts)
        if tool_failures:
            reasons.append(f"tool failure: {', '.join(tool_failures)}")
        if matching_policy_count > 1:
            reasons.append(f"{matching_policy_count} active precedents match; precedents conflict")
        return GuardrailResult(passed=not reasons, reasons=tuple(reasons))

    return guardrails


# ---------------------------------------------------------- past answers


def label_for(contract: WorkstreamContract, answer: str) -> str:
    """A past answer as a replay label: the repeatable answer is what a follow would do."""
    return LABEL_AUTO if answer == contract.repeatable_answer else LABEL_ESCALATE


ANSWER_PREFIX = "answer:"


def make_corpus(contract: WorkstreamContract) -> Callable[["Connection"], list[tuple[str, str, str]]]:
    """Past answers in this workstream, each to be replayed on its own facts.

    Ids are "answer:<answer_id>", which `derive_facts` resolves to the facts
    snapshot taken when that answer was given, never to the latest row in
    `harness_cases`: a later arrival under the same case_ref cannot rewrite what
    a person decided. One per question: a person's latest answer to a decision
    (or to a template's past case) wins.
    """
    prefix = history_decision_id(contract.id, "")

    def corpus(conn: "Connection") -> list[tuple[str, str, str]]:
        latest: dict[str, tuple[str, str, str, str]] = {}  # question -> (answer_id, case_ref, answer, note)
        rows = conn.execute(
            """SELECT answer_id, decision_id, answer, note FROM harness_answers
                WHERE decision_id LIKE ? ORDER BY created_at, answer_id""",
            (f"{prefix}%",),
        ).fetchall()
        for row in rows:
            if row["decision_id"].startswith(prefix):
                latest[row["decision_id"]] = (
                    row["answer_id"], row["decision_id"][len(prefix):], row["answer"],
                    row["note"] or "past answer",
                )
        rows = conn.execute(
            """SELECT a.answer_id, a.decision_id, d.case_ref, a.answer, a.note FROM harness_answers a
                 JOIN harness_decisions d ON d.decision_id = a.decision_id
                WHERE d.workstream = ? ORDER BY a.created_at, a.answer_id""",
            (contract.id,),
        ).fetchall()
        for row in rows:
            latest[row["decision_id"]] = (
                row["answer_id"], row["case_ref"], row["answer"], row["note"] or "answered by a person",
            )
        return [
            (f"{ANSWER_PREFIX}{answer_id}", label_for(contract, answer), f"{case_ref} answered '{answer}': {note}")
            for answer_id, case_ref, answer, note in latest.values()
        ]

    return corpus


def answer_facts(conn: "Connection", contract: WorkstreamContract, corpus_id: str) -> cases.PlantedFacts:
    """The facts one past answer was given on, from its own snapshot."""
    answer_id = corpus_id[len(ANSWER_PREFIX):]
    row = conn.execute("SELECT facts FROM harness_answers WHERE answer_id = ?", (answer_id,)).fetchone()
    if row is None:
        raise LookupError(f"no answer '{answer_id}'")
    values = cases.snapshot(contract, json.loads(row["facts"] or "{}"))
    return cases.PlantedFacts(
        case_id=corpus_id, values=cases.full_view(contract, values), source=cases.SOURCE_ANSWER
    )


# ------------------------------------------------------------- the spec


def make_policy_text(contract: WorkstreamContract) -> Callable[[Any], str]:
    def policy_text(definition: Any) -> str:
        conditions = "; ".join(phrase(contract, c) for c in definition.conditions)
        always = "; ".join(phrase(contract, c) for c in contract.always_ask)
        text = (
            f'{contract.label}: precedent "{definition.name}". A {contract.subject} may be '
            f'answered "{contract.repeatable_answer}" automatically only when ALL of these hold: '
            f"{conditions}. If any condition fails or cannot be established from the evidence, "
            "a person must answer it."
        )
        if always:
            text += f" A person always answers when: {always}."
        return text

    return policy_text


def make_evidence_text(contract: WorkstreamContract) -> Callable[["Connection", str], str]:
    def evidence_text(conn: "Connection", case_ref: str) -> str:
        """The case exactly as the caller sent it: its text, never a reading of it."""
        stored = cases.load(conn, contract.id, case_ref)
        if stored is None or not (stored.text or "").strip():
            raise LookupError(f"{contract.subject} {case_ref} has no text to check")
        return f"{contract.label}, {contract.subject} {case_ref}, as sent:\n{stored.text.strip()}"

    return evidence_text


def _the_caller_executes(*_: Any, **__: Any) -> Any:
    raise NotImplementedError("the caller executes")


def build_spec(contract: WorkstreamContract) -> DomainSpec:
    amount = contract.amount_field
    param = cap_param(contract)
    action = ActionSpec(
        type=ANSWER_ACTION,
        literals={"answer": (contract.repeatable_answer,)},
        caps={param: float(amount.ceiling)} if amount and param else {},
        bound_by=(param, amount.name) if amount and param else None,
    )

    def fixed_actions(cap: float) -> list[dict[str, Any]]:
        out: dict[str, Any] = {"type": ANSWER_ACTION, "answer": contract.repeatable_answer}
        if amount is not None and param is not None:
            out[param] = min(float(cap), float(amount.ceiling))
        return [out]

    def derive_facts(conn: "Connection", case_ref: str) -> cases.PlantedFacts:
        if case_ref.startswith(ANSWER_PREFIX):  # a past answer, replayed on its own snapshot
            return answer_facts(conn, contract, case_ref)
        return cases.facts_for(conn, contract, case_ref)

    with_evidence = contract.evidence == "text"
    return DomainSpec(
        family=contract.id,
        label=contract.label,
        subject=contract.subject,
        bool_fields=frozenset(f.name for f in contract.fields if f.kind == "flag"),
        number_fields=frozenset(f.name for f in contract.fields if f.kind in ("amount", "count")),
        enum_fields={
            f.name: frozenset(f.values or []) for f in contract.fields if f.kind == "category"
        },
        numeric_ceilings={
            f.name: float(f.ceiling)
            for f in contract.fields
            if f.kind in ("amount", "count") and f.ceiling is not None
        },
        actions=(action,),
        required_action_types=(ANSWER_ACTION,),
        derive_facts=derive_facts,
        guardrails=make_guardrails(contract),
        corpus=make_corpus(contract),
        execute=_the_caller_executes,
        verify=_the_caller_executes,
        fixed_actions=fixed_actions,
        policy_text=make_policy_text(contract) if with_evidence else None,
        evidence_text=make_evidence_text(contract) if with_evidence else None,
        question=contract.question,
        repeatable_answer=contract.repeatable_answer,
        other_answers=tuple(contract.other_answers),
        audience=contract.audience,
        reader=contract.reader,
    )
