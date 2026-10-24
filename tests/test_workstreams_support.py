"""Shared helpers for the workstream tests. No tests live here.

`FakeDecisionService` stands in for builder A's `app.harness.service.decide`: it
runs the registered engine for the workstream inside one transaction and files
the result in `harness_decisions`, the way the real service does, so the
decide-once loop can be exercised end to end without it.
"""

from __future__ import annotations

import copy
import json
import sys
import types
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

APPROVAL = "test-approval-token"
DANA = "dana.r@wharf-canary-optics.example"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class FakeDecisionService:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, str | None]] = []

    def decide(self, request, *, resolves: str | None = None) -> dict[str, Any]:
        from app import db
        from app.agent.providers import CallLog
        from app.harness import engines

        self.calls.append((request, resolves))
        with db.session() as conn:
            found = conn.execute(
                "SELECT * FROM harness_decisions WHERE workstream = ? AND idempotency_key = ?",
                (request.workstream, request.idempotency_key),
            ).fetchone()
            if found:
                return {"decision_id": found["decision_id"], "verdict": found["verdict"],
                        "reasons": json.loads(found["reasons"]), "resolves": found["resolves"]}
            trace_id = f"trc_{uuid.uuid4().hex[:12]}"
            calls = CallLog(request.workstream, request.case_ref)
            assessment = engines.engine_for(request.workstream).assess(
                conn, request, trace_id=trace_id, calls=calls
            )
            decision_id = f"dec_{uuid.uuid4().hex[:12]}"
            conn.execute(
                """INSERT INTO harness_decisions (decision_id, workstream, case_ref, idempotency_key,
                       input_hash, verdict, precedent_id, precedent_version, actions, reasons, checks,
                       model_calls, caller, trace_id, resolves, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision_id, request.workstream, request.case_ref, request.idempotency_key,
                    "sha256:test", assessment.verdict, assessment.precedent_id,
                    assessment.precedent_version, json.dumps(list(assessment.actions)),
                    json.dumps(list(assessment.reasons)),
                    json.dumps([c.to_dict() for c in assessment.checks]), "[]",
                    request.caller, trace_id, resolves, now(),
                ),
            )
        return {
            "decision_id": decision_id,
            "verdict": assessment.verdict,
            "reasons": list(assessment.reasons),
            "checks": [c.to_dict() for c in assessment.checks],
            "precedent_id": assessment.precedent_id,
            "actions": list(assessment.actions),
            "facts": assessment.facts,
            "resolves": resolves,
        }


@pytest.fixture()
def fake_service(monkeypatch):
    """Install the fake as `app.harness.service` for the length of a test."""
    import app.harness as harness

    fake = FakeDecisionService()
    module = types.ModuleType("app.harness.service")
    module.decide = fake.decide  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.harness.service", module)
    monkeypatch.setattr(harness, "service", module, raising=False)
    return fake


@pytest.fixture()
def isolated_registry():
    """Planted workstreams register in process memory; forget them after each test."""
    from app import domains
    from app.harness import engines
    from app.workstreams.contract import BUILT_IN_IDS
    from app.workstreams.engine import PlantedEngine

    domains.load()
    before_domains = set(domains.families())
    before_engines = set(engines.families())
    yield
    # Only what a test planted: the built-in engines register once per process
    # (app.harness.bootstrap), and forgetting them would break later tests.
    for family in set(domains.families()) - before_domains - BUILT_IN_IDS:
        domains.unregister(family)
    for family in set(engines.families()) - before_engines:
        if isinstance(engines._ENGINES.get(family), PlantedEngine):
            engines._ENGINES.pop(family, None)


def template(template_id: str) -> dict[str, Any]:
    from app.workstreams import service

    return copy.deepcopy(service.load_template(template_id))


def incoming(template_id: str, case_ref: str) -> dict[str, Any]:
    return next(i for i in template(template_id)["incoming"] if i["case_ref"] == case_ref)


def send(fake: FakeDecisionService, workstream: str, case_ref: str, *, facts=None, text=None,
         key: str | None = None) -> dict[str, Any]:
    """A caller sends one case, as the traffic script does."""
    from app.harness.engines import DecideRequest

    return fake.decide(
        DecideRequest(
            workstream=workstream,
            case_ref=case_ref,
            idempotency_key=key or f"evt-{uuid.uuid4().hex[:8]}",
            facts=facts,
            text=text,
        )
    )


def send_incoming(fake: FakeDecisionService, template_id: str, case_ref: str, **overrides) -> dict[str, Any]:
    item = incoming(template_id, case_ref)
    facts = {**item["facts"], **overrides}
    return send(fake, template_id, case_ref, facts=facts, text=item["text"])
