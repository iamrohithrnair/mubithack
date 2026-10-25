"""The workstream contract: what a person or team declares when planting one.

Field names here are frozen (docs/decide-once-plan.md, 5.2). The validators below
only add rules a contract must meet to be governable: a category lists its
values, an amount has a ceiling, the repeatable answer is not also a "never
repeat" answer. `always_ask` conditions are checked against the workstream's own
fields by `app.workstreams.registry.parse_contract`, because the policy
language validates a condition against the registered domains.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.policy.schema import Condition

FieldKind = Literal["flag", "category", "amount", "count"]

# Ids that belong to the built-in workstreams, which keep their own DomainSpec.
BUILT_IN_IDS = frozenset({"returns.missing_accessory", "ap.invoice_variance"})


class FieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    kind: FieldKind
    label: str = Field(min_length=1, max_length=80)
    values: list[str] | None = None  # category only
    ceiling: float | None = None  # amount and count: no precedent may exceed it
    required: bool = True
    # Optional plain-English phrases for a flag, used in a precedent's text:
    # "a receipt is attached" / "there is no receipt". Without them the label is used.
    when_true: str | None = Field(default=None, max_length=80)
    when_false: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def _kind_rules(self) -> "FieldSpec":
        if self.kind == "category":
            if not self.values:
                raise ValueError(f"category field '{self.name}' must list its values")
            if len(set(self.values)) != len(self.values):
                raise ValueError(f"category field '{self.name}' lists a value twice")
            for value in self.values:
                if not value or len(value) > 40 or value != value.strip():
                    raise ValueError(f"category field '{self.name}' has an invalid value {value!r}")
        elif self.values is not None:
            raise ValueError(f"only a category field lists values; '{self.name}' is a {self.kind}")

        if self.kind in ("amount", "count"):
            if self.ceiling is not None and self.ceiling <= 0:
                raise ValueError(f"field '{self.name}' needs a ceiling above zero")
            if self.kind == "amount" and self.ceiling is None:
                raise ValueError(
                    f"amount field '{self.name}' needs a ceiling: no precedent may approve more"
                )
        elif self.ceiling is not None:
            raise ValueError(f"only an amount or count field has a ceiling; '{self.name}' is a {self.kind}")

        if self.kind != "flag" and (self.when_true or self.when_false):
            raise ValueError(f"only a flag field takes when_true/when_false; '{self.name}' is a {self.kind}")
        return self


class WorkstreamContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")  # "expenses.meal_claim"
    label: str = Field(min_length=3, max_length=80)
    subject: str = Field(min_length=2, max_length=40)  # "meal claim"
    question: str = Field(min_length=5, max_length=160)
    owner: str = Field(min_length=1, max_length=120)
    audience: Literal["team", "individual"] = "team"
    fields: list[FieldSpec] = Field(min_length=1, max_length=12)
    repeatable_answer: str = Field(min_length=2, max_length=40)
    other_answers: list[str] = Field(default_factory=list)
    always_ask: list[Condition] = Field(default_factory=list)
    reader: bool = False  # the model reads `text` into fields, through Mubit
    evidence: Literal["none", "text"] = "none"  # "text": Sapling must confirm before a follow
    version: int = 1

    @model_validator(mode="after")
    def _contract_rules(self) -> "WorkstreamContract":
        if self.id in BUILT_IN_IDS:
            raise ValueError(f"'{self.id}' is a built-in workstream; choose another id")
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("each field name may appear once")
        answers = [self.repeatable_answer, *self.other_answers]
        if any(not a.strip() or len(a) > 40 for a in answers):
            raise ValueError("answers must be 1 to 40 characters")
        if len(set(answers)) != len(answers):
            raise ValueError("the repeatable answer and the other answers must all differ")
        if self.version < 1:
            raise ValueError("version starts at 1")
        return self

    def field_named(self, name: str) -> FieldSpec | None:
        return next((f for f in self.fields if f.name == name), None)

    @property
    def answers(self) -> list[str]:
        return [self.repeatable_answer, *self.other_answers]

    @property
    def amount_field(self) -> FieldSpec | None:
        """The amount that bounds a follow's cap: the first amount field, if any."""
        return next((f for f in self.fields if f.kind == "amount"), None)
