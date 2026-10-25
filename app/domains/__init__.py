"""What a domain has to supply, and the registry of domains.

The governance machinery — the policy language, the engine, the replay gate, the
activation gate, execution, verification, the audit log — knows nothing about
returns or invoices. It knows that *some* domain supplies:

  * a set of allowlisted fact fields, with their types and hard ceilings,
  * a fixed set of actions a policy may take, with their caps,
  * deterministic facts for one case,
  * non-overridable guardrails,
  * a labeled historical corpus to replay against,
  * an executor and a verifier for the actions.

A domain is data plus four functions. It is not a subclass, and it cannot reach
into the gate: nothing here can relax a rule, because there is no field in which
to express one. Adding a domain does not touch the machinery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Protocol

if TYPE_CHECKING:
    from app.db import Connection


class Facts(Protocol):
    """The shape every domain's fact record has to satisfy."""

    case_id: str

    def policy_view(self) -> dict[str, object]:
        """The subset a policy engine may read. Nothing else is visible to it."""


@dataclass(frozen=True)
class ActionSpec:
    """One action a policy of this family may take, and its bounds.

    `literals` pins a parameter to a fixed set of values: a policy may pick from
    them and nothing else. `caps` bounds a numeric parameter, and `bound_by`
    names the fact field whose condition must back that cap, so a policy can
    never authorize more than the condition it was replayed under.
    """

    type: str
    literals: dict[str, tuple[str, ...]] = field(default_factory=dict)
    caps: dict[str, float] = field(default_factory=dict)
    bound_by: tuple[str, str] | None = None  # (cap parameter, fact field)

    def describe(self, params: dict[str, Any]) -> str:
        rendered = ", ".join(
            f"{k} {v:.2f}" if isinstance(v, float) else f"{k} {v}"
            for k, v in params.items()
            if k != "type"
        )
        return f"{self.type} ({rendered})" if rendered else self.type


@dataclass(frozen=True)
class DomainSpec:
    """Everything the machinery needs to govern one kind of exception."""

    family: str
    label: str
    subject: str  # what one case is called, for prose: "return case", "invoice"

    bool_fields: frozenset[str]
    number_fields: frozenset[str]
    enum_fields: dict[str, frozenset[str]]
    numeric_ceilings: dict[str, float]

    actions: tuple[ActionSpec, ...]
    required_action_types: tuple[str, ...]

    derive_facts: Callable[["Connection", str], Facts]
    guardrails: Callable[..., Any]
    corpus: Callable[["Connection"], list[tuple[str, str, str]]]  # case_id, label, note
    execute: Callable[..., list[Any]]
    verify: Callable[..., Any]
    fixed_actions: Callable[[float], list[dict[str, Any]]]

    # Plain-language renderings for the Sapling semantic check (app/sapling.py).
    # A domain without them is not semantically checked.
    policy_text: Callable[[Any], str] | None = None
    evidence_text: Callable[["Connection", str], str] | None = None

    # How the workstream reads to a person (docs/decide-once-plan.md, 5.2). The
    # repeatable answer is the one answer a precedent may repeat; it is a label
    # for people, and the action set above stays what actually bounds a follow.
    question: str = ""
    repeatable_answer: str | None = None
    other_answers: tuple[str, ...] = ()
    audience: str = "team"
    reader: bool = False  # a model reads each case before the rules run

    @property
    def allowed_fields(self) -> frozenset[str]:
        return self.bool_fields | self.number_fields | frozenset(self.enum_fields)

    def action(self, action_type: str) -> ActionSpec | None:
        return next((a for a in self.actions if a.type == action_type), None)

    def cap_of(self, actions: list[Any]) -> float:
        """The spend or adjustment cap this policy authorizes, if it has one."""
        for action in actions:
            spec = self.action(action.get("type") if isinstance(action, dict) else action.type)
            if spec and spec.bound_by:
                param = spec.bound_by[0]
                value = action.get(param) if isinstance(action, dict) else getattr(action, param, None)
                if value is not None:
                    return float(value)
        return 0.0


_REGISTRY: dict[str, DomainSpec] = {}


def register(spec: DomainSpec) -> DomainSpec:
    if spec.family in _REGISTRY:
        raise ValueError(f"domain '{spec.family}' is already registered")
    _REGISTRY[spec.family] = spec
    return spec


def upsert(spec: DomainSpec) -> DomainSpec | None:
    """Register a planted workstream's domain, replacing any earlier version.

    Built-in packs register once with `register`; a planted workstream comes from
    a stored contract and re-registers whenever the process loads it. Returns the
    spec it replaced, so a caller validating a contract can put it back.
    """
    previous = _REGISTRY.get(spec.family)
    _REGISTRY[spec.family] = spec
    return previous


def unregister(family: str) -> DomainSpec | None:
    """Forget a planted workstream's domain. Used to undo a provisional registration."""
    return _REGISTRY.pop(family, None)


def get(family: str) -> DomainSpec:
    try:
        return _REGISTRY[family]
    except KeyError:
        raise KeyError(
            f"unknown policy family '{family}'; registered: {sorted(_REGISTRY)}"
        ) from None


def families() -> list[str]:
    return sorted(_REGISTRY)


def all_specs() -> list[DomainSpec]:
    return [_REGISTRY[f] for f in families()]


def load() -> None:
    """Import every domain pack, so registration happens exactly once."""
    from app.domains import ap_invoices, returns  # noqa: F401
