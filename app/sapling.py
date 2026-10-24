"""Semantic verification with Banyan Sapling, the last check before money moves.

The policy engine reads structured fields and nothing else. That is what makes it
safe — and what makes it blind: an inspector who writes "battery door scorched,
form already submitted" in the free-text note leaves `new_damage_present` false,
and the engine would approve the case.

Sapling reads what the engine cannot. Right before an approved policy executes,
it is handed three things:

  policy   - the matched policy, in plain language,
  evidence - the raw source-system record, free-text notes included, rendered
             by the domain (never the agent's report),
  claim    - "this case meets every condition and may be resolved automatically".

Only a `supported` verdict lets the action run. `violation`,
`insufficient_information`, `inconsistent_context`, a low-confidence `supported`,
or an unreachable endpoint all escalate the case to a person. Like every other
check here, Sapling can only make the system more conservative: it can stop an
action the engine allowed, never allow one the engine refused.

Sapling's calibrated `status` is conservative enough to return `unsure` on
nearly every case in this domain, so the gate reads the raw label and its
probability, and records the calibrated status in the audit trail alongside.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings, get_settings

MODEL_LABEL = "Banyan Sapling"
SUPPORTED = "supported"
LABELS = (SUPPORTED, "violation", "insufficient_information", "inconsistent_context")

CLAIM = "This case meets every condition of the policy and may be resolved automatically."


@dataclass(frozen=True)
class SaplingVerdict:
    raw_label: str | None
    status: str | None
    probabilities: dict[str, float] = field(default_factory=dict)
    latency_ms: int = 0
    error: str | None = None

    @property
    def support(self) -> float:
        return float(self.probabilities.get(SUPPORTED, 0.0))

    def passed(self, min_support: float) -> bool:
        return self.error is None and self.raw_label == SUPPORTED and self.support >= min_support

    def reason(self, min_support: float) -> str:
        """Why the verdict blocks automatic action, in one line for a person."""
        if self.error is not None:
            return f"{MODEL_LABEL} semantic check unavailable ({self.error}); failing closed"
        top = self.probabilities.get(self.raw_label or "", 0.0)
        if self.raw_label == SUPPORTED:
            return (
                f"{MODEL_LABEL} supports the claim only weakly "
                f"(p={self.support:.2f}, needs {min_support:.2f})"
            )
        return (
            f"{MODEL_LABEL} found the evidence does not support automatic resolution: "
            f"{(self.raw_label or 'unknown').replace('_', ' ')} (p={top:.2f})"
        )

    def payload(self) -> dict[str, Any]:
        return {
            "model": MODEL_LABEL,
            "raw_label": self.raw_label,
            "calibrated_status": self.status,
            "probabilities": {k: round(v, 4) for k, v in self.probabilities.items()},
            "latency_ms": self.latency_ms,
            "error": self.error,
        }


def verify(
    policy: str, evidence: str, claim: str = CLAIM, *, settings: Settings | None = None
) -> SaplingVerdict:
    """Ask Sapling whether `evidence` supports `claim` under `policy`. Never raises."""
    cfg = settings or get_settings()
    body = json.dumps({"inputs": {"policy": policy, "evidence": evidence, "claim": claim}})
    request = urllib.request.Request(
        cfg.sapling_url,
        data=body.encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=cfg.sapling_timeout_seconds) as response:
            out = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return SaplingVerdict(
            None, None, latency_ms=_ms(started), error=f"{type(exc).__name__}: {exc}"
        )
    return parse_response(out, latency_ms=_ms(started))


def parse_response(out: Any, *, latency_ms: int = 0) -> SaplingVerdict:
    if not isinstance(out, dict) or out.get("error"):
        detail = out.get("error") if isinstance(out, dict) else "malformed response"
        return SaplingVerdict(None, None, latency_ms=latency_ms, error=str(detail))
    raw_label = out.get("raw_label")
    if raw_label not in LABELS:
        return SaplingVerdict(
            None, None, latency_ms=latency_ms, error=f"unexpected label {raw_label!r}"
        )
    probabilities = {
        k: float(v)
        for k, v in (out.get("probabilities") or {}).items()
        if k in LABELS and isinstance(v, (int, float))
    }
    return SaplingVerdict(
        raw_label=raw_label,
        status=out.get("status"),
        probabilities=probabilities,
        latency_ms=latency_ms,
    )


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
