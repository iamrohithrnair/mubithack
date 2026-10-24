# Precedenz - Product scope

Last updated: 2026-10-24.

**Tagline:** Teach the agent once; it safely handles the next hundred decisions.

---

## 1. The problem

Rule-based automation handles the cases somebody already wrote down. Everything
else becomes an interruption. A returns supervisor at a small electronics
reseller is pinged twenty times a week with variations of the same question,
answers each one in thirty seconds, and never gets the time to turn that
judgment into a written policy — let alone into automation.

Precedenz closes that loop. It asks a human **once**, converts the answer into
a constrained, versioned, machine-checkable policy, proves the policy against
historical cases before anyone trusts it, and then handles matching cases
without interrupting anybody. Anything outside the approved boundary escalates.

## 2. The one exception family (MVP boundary)

**Missing-accessory exceptions discovered while checking in returned
high-value camera / electronics kits.**

Nothing else in the UI or the agent loop. Not refunds. Not shipping. Not a generic
workflow builder. A second exception family, accounts-payable invoice variance,
exercises the shared governance path and its tests; see the README.

## 3. Fictional company and synthetic data

All data in this repository is invented for the demo.

| Thing | Value |
| --- | --- |
| Company | **Wharf Canary Optics** (fictional camera and optics rental / resale) |
| Facility | `WCO-WH-1`, Returns & Inspection dock |
| Persona | Dana R., Returns Supervisor — the human who decides |
| Products | Synthetic SKUs (`NGO-KIT-*`), synthetic serials, synthetic orders |
| Customers | Synthetic names and IDs; **no real people, no PII** |

Every business system Precedenz touches is a **synthetic adapter** in
`app/adapters/` - an in-repo simulation over SQLite or PostgreSQL. They are labeled as
synthetic in the UI and in code. They are **not** production integrations, and
nothing in this project talks to a real WMS, ERP, payment system, or customer.

## 4. The golden path (the thing that must work)

```
synthetic exception event
  -> real Strands agent investigates with tools
  -> deterministic fact reconciliation
  -> no approved policy applies
  -> agent emits a typed decision card
  -> human clicks "Approve and Teach"
  -> agent proposes a constrained policy (allowlisted schema only)
  -> deterministic validation + replay against historical cases
  -> human explicitly activates the policy version
  -> next matching case resolves automatically (work order + disposition)
  -> deterministic verification of the written state
  -> append-only audit entry for every step
```

## 5. Safety architecture (non-negotiable)

The separation between *reasoning* and *acting* is the product.

1. **The model may propose. The model may never activate.** Activation is a
   human HTTP action carrying an approval token.
2. **Deterministic code decides, not the model.** The agent's investigation is
   advisory. Facts are re-derived from the adapters and reconciled against the
   agent's report; a mismatch escalates.
3. **Policies are data, not code.** A policy is a Pydantic/JSON structure with
   an allowlist of fields, operators, value types, thresholds, and actions.
   Anything off-allowlist fails validation.
4. **No generated execution.** Precedenz never executes generated Python,
   SQL, shell, or natural-language conditions. There is no `eval`.
5. **Hard invariants outrank policies.** A set of non-overridable guardrails
   (`app/policy/guardrails.py`) is evaluated before any policy. A policy can
   only ever *narrow* automation, never widen it.
6. **Replay gates activation.** A candidate policy cannot be activated until it
   has been replayed against the labeled historical case set with zero false
   automatic actions. A **human revision** goes through the identical gate — a
   person can tighten what the agent proposed, or loosen it within the hard
   guardrails, but cannot activate either without re-proving it against history.
   The one part of the gate that is about quality rather than safety — the
   automation-coverage floor — is waived for a revision that is no *wider* than
   what it revises, because refusing someone who wants to be more conservative
   is exactly backwards. Zero false automatic actions is never waived.
7. **A revision cannot remove a safety condition.** It adjusts a spend cap, a
   confidence floor, and an optional category restriction. There is no field in
   which to express removing a condition or adding an action.
8. **Default to escalation.** Ambiguity, missing evidence, tool failure, model
   timeout, low confidence, or conflicting policies all escalate.
9. **Idempotency everywhere.** Every state-changing action carries an
   idempotency key; replays are no-ops that return the original result.
10. **Append-only audit.** The audit log is hash-chained and protected by database
   triggers that reject `UPDATE` and `DELETE` — and, on PostgreSQL, by a `REVOKE`
   so a non-owner application role cannot rewrite history at all.
11. **Banyan Sapling can veto an action.** In the hosted returns workflow, it
    checks the raw source record, including inspector notes, against the policy.
    A failed or unavailable check escalates; it cannot authorize an action the
    deterministic gates rejected.
12. **No hidden chain-of-thought.** The agent returns a short, structured
    rationale intended for a human reader. Raw reasoning is never surfaced or
    stored.

### Always escalate

Serial mismatch · incomplete evidence · new damage · missing or excessive
replacement cost · serialized, safety-critical, or essential missing component ·
more than one missing component · conflicting policies · tool failure ·
confidence below threshold; a blocked or unavailable semantic check when Sapling
is enforced.

## 6. Explicitly out of scope

Real refunds, payments, email, or
third-party integrations · multi-tenancy · authentication beyond a demo
approval token · mobile app · generic workflow builder · arbitrary
natural-language automation · RAG or vector databases · multi-agent swarms ·
generated executable code · computer vision or OCR · billing.

## 7. Architecture decisions

| # | Decision | Rationale | Reversible? |
| --- | --- | --- | --- |
| 1 | Python 3.11 + FastAPI + Jinja2, PostgreSQL in deployment and SQLite for the local demo | Zero-build and server-rendered; the demo needs no database server, a deployment gets a real one. Both support the same suite. See docs/database.md | Yes |
| 2 | Exactly one Strands agent | The product is governance, not agent count | Yes |
| 3 | Model provider behind an adapter (`app/agent/providers/`) | Demo calls use the Mubit gateway; tests inject a model fake without changing the safety path | Yes |
| 4 | A deterministic test-only Strands `Model` fake | Gives hermetic regression tests while exercising the real Strands agent loop and real tool calls; it is not an app provider | Yes |
| 5 | Policy DSL as a closed Pydantic schema | Makes "the model cannot widen its own authority" a type error, not a prompt instruction | Hard to reverse (core) |
| 6 | Facts re-derived deterministically, agent report reconciled | Prevents a hallucinated or injected fact from driving an action | Hard to reverse (core) |
| 7 | Hash-chained append-only audit, serialized by an advisory lock and guarded by `UNIQUE(prev_hash)` | Tamper-evidence that survives more than one writer | Yes |
| 8 | Hosted model access stays separate from business-system writes | Changing the model provider cannot change its authority or bypass the policy gates | Yes |
| 9 | A revision creates a new candidate rather than editing one | The record keeps what the agent proposed alongside what the human changed; lineage is auditable | Hard to reverse (core) |

## 8. Assumptions

- **A1.** Anyone with a Mubit key may run the project locally with
  `make setup && make seed && make run`. `make test` needs no model credentials.
- **A2.** The hosted demo uses `PRECEDENZ_MODEL_PROVIDER=mubit` and a Mubit key.
  Its model calls need network access; test-only fakes do not.
- **A3.** "One approved non-serialized accessory" means exactly one missing
  line item, quantity one.
- **A4.** Replacement cost is authoritative from the synthetic parts catalog,
  not from the model.
- **A5.** A single human role (returns supervisor). No user accounts; the
  approval token stands in for real authorization.
