"""The workstream contract: what a person or team declares when planting one."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.policy.schema import Condition

FieldKind = Literal["flag", "category", "amount", "count"]


class FieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    kind: FieldKind
    label: str = Field(min_length=1, max_length=80)
    values: list[str] | None = None  # category only
    ceiling: float | None = None  # amount and count: no precedent may exceed it
    required: bool = True


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
