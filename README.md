# Precedenz

**Teach the agent once; it safely handles the next hundred decisions.**

Hosted returns workflow: Strands Agents on gpt-6-luna through the **Mubit** gateway,
with **Banyan Sapling** verifying the evidence before automatic action.

Last updated: 2026-10-24.

![Precedenz architecture](docs/architecture.png)

For the interactive version, open [docs/architecture.html](docs/architecture.html) in a
browser from a local clone (GitHub shows it as source) and hover any component to see what
it connects to and which way the data moves. Both views render from `docs/architecture.json`.

---

## Decide once, anywhere

> **Never answer the same question twice.**
> **It doesn't make decisions. It repeats yours.**
> **If it can't check it, it asks.**

Precedenz now plugs into any recurring decision, for a team, an institution, or one
person. A *workstream* is a contract (typed fields, ceilings, the one answer a precedent
may repeat, always-ask rules) plus past answers. Plant one from a template; no code.

- **Follow or ask.** `POST /v1/decide` returns `follow` (repeat a decision a person already
  made, as a bounded action the caller executes) or `ask` (what differs, or what couldn't
  be checked). Precedenz never writes to a business system.
- **Decide once.** A person answers an ask and remembers it. The precedent is drafted
  exactly from that answer, replayed against past answers, activated with the approval
  token, and the waiting questions it covers resolve on their own. Overrule stops it at once.
- **Every way in.** HTTP, the Python client, the `precedenz` CLI, and `precedenz-mcp` for AI
  agents (it can ask; only a person can answer). See [docs/integrations.md](docs/integrations.md).
- **Two loops.** Precedenz learns from one answer. Every model call goes through the Mubit
  gateway as one stable task per workstream with a session per case, and each receipt
  carries its `x-middleware-request-id`s.
- **The map.** `/map` is a live bioluminescent dendrogram: Dana at the center, workstreams,
  precedents, and every case on the rim. Every pulse is a real decision.

```bash
PRECEDENZ_DB_PATH=var/demo.db .venv/bin/python scripts/demo_setup.py   # fresh data, two precedents
PRECEDENZ_DB_PATH=var/demo.db make run                                  # open http://127.0.0.1:8000/map
.venv/bin/precedenz plant expenses.meal_claim                           # a new workstream, live
.venv/bin/python scripts/demo_traffic.py --workstream expenses.meal_claim --count 6
.venv/bin/precedenz open                                                # what waits for a person
.venv/bin/precedenz answer <decision_id> approve --remember --up-to amount_usd=75 --ignore client_meeting --ignore attendees  # asks for the approval token
.venv/bin/precedenz watch                                               # the live event tail
```

Templates: `expenses.meal_claim` (team), `calendar.meeting_invite` (one person),
`it.access_request` (team). The build plan is [docs/decide-once-plan.md](docs/decide-once-plan.md).

---

## The problem

Rule-based automation handles the cases someone already wrote down. Everything else
becomes an interruption.

A returns supervisor at a small electronics reseller gets pinged twenty times a week
with variations of the same question. *A kit came back missing its lens cap, what do I
do?* She answers each one in thirty seconds, and never gets the hour it would take to
turn that judgment into a written policy, let alone into automation. The knowledge stays
in their head. The interruptions keep coming.

## What Precedenz does

It closes that loop, once:

1. An unfamiliar exception arrives **as an event**, not a chat prompt.
2. A Strands agent investigates it with tools and reconciles the evidence.
3. No approved policy covers it, so the agent produces **one compact decision card**:
   evidence, recommendation, what it is unsure about, and the boundaries it thinks the
   decision should live inside.
4. The supervisor clicks **Approve and Teach**.
5. The agent proposes a **tightly bounded, schema-validated policy**. It is inert.
6. Deterministic code **replays** the candidate against 24 labeled historical cases.
7. A **policy diff** shows exactly what changes, and flags anything that gets
   *wider*. The supervisor can **revise** the boundary (tighten the spend cap,
   raise the confidence floor, restrict it to one kit category), which creates a
   new candidate that is replayed again from scratch.
8. The supervisor **explicitly activates** the version.
9. The next matching case resolves automatically: work order raised, disposition set,
   writes verified, exception closed against the policy version.
10. A risky near-match still escalates.
11. Every step is in an append-only, hash-chained audit log.

Every model call on that path takes tens of seconds, so the page reports what the run is
doing while it runs: each phase and each tool call, with real timings, as the server
reaches them. A forty-second wait that shows nothing reads as a hung app, and a
supervisor who thinks the tool is broken will not trust what it says next.

This is not an agent that writes SOPs. It converts recurring human judgment into
governed, testable automation, and it can prove what that automation would have done
before anyone trusts it.

---

## Run with Mubit

Install the dependencies with `make setup`. If you do not already have a `.env`,
copy `.env.example` to `.env`, then set:

```dotenv
PRECEDENZ_MODEL_PROVIDER=mubit
MUBIT_API_KEY=your-mubit-key
PRECEDENZ_MUBIT_MODEL_ID=gpt-6-luna
PRECEDENZ_SAPLING_MODE=enforce
```

Start the synthetic demo:

```bash
make seed
make run       # http://127.0.0.1:8000
```

Every Strands model call in this configuration goes through
`https://api.mubit.ai/openai/v1`. Banyan Sapling uses its separately configured
verification endpoint. Hosted runs need network access and may incur usage charges.
Provider keys, when required, belong in the Mubit console, not in requests from
Precedenz. Never commit `.env`.

**What teaching changes today:** Approve and Teach creates a replay-tested policy
version. It does not fine-tune model weights or activate a Mubit adapter.

## Demo walkthrough

Then, in the app: check in **CASE-2001** → *Approve and teach* → activate the policy →
check in **CASE-2002** (resolves itself) → check in **CASE-2003** (escalates).

The **Dashboard** tab shows the history and usage behind it: cases by outcome, the share
handled automatically, human decisions and policy activations, agent runs, tool calls by
tool, and model tokens when the provider reports them. Every number is counted from the
cases table, the policy store, and the audit log.

Activating a policy asks for an approval token, because a human authorizing
automation is the whole point. On a fresh clone that token is:

```
replace-me-local-demo-token
```

It is a placeholder, not a secret, and the activation screen prints it for you.
Set `PRECEDENZ_APPROVAL_TOKEN` to replace it, and the on-screen notice disappears
when you do.

The app and synthetic business systems run locally. Model calls use Mubit and
require a Mubit key.

### On PostgreSQL, the deployment target

```bash
make db-up     # docker compose PostgreSQL + migrations + seed
make test-pg   # the whole suite against BOTH backends
make run
```

If 5432 is already taken on your machine, publish the container somewhere else and
everything follows, including both connection strings:

```bash
PRECEDENZ_PG_PORT=55432 make db-up
PRECEDENZ_PG_PORT=55432 make test-pg
```

Or point at any PostgreSQL and the app follows:

```bash
export PRECEDENZ_DATABASE_URL=postgresql://user:pass@host:5432/precedenz
make migrate && make seed && make run
```

Or watch the whole thing in the terminal:

```bash
make demo      # the golden path, start to finish
make test      # offline regression suite with test-only model fakes
make eval      # hosted evaluation -> docs/evaluation-results.md; uses API credits
make smoke     # configured hosted model + real tool calls + typed output
```

`make seed` is also the **Reset Demo** button in the UI.

---

## Is the agent real?

Yes. `make smoke` prints it:

```
tool calls made by the agent:
  [ok  ] get_return_case({'case_id': 'CASE-2001'}) -> Meridian 4100 Mirrorless Body Kit ...
  [ok  ] compare_expected_and_received({'case_id': 'CASE-2001'}) -> 1 missing, serial matches ...
  [ok  ] lookup_replacement_cost({'component_id': 'CMP-STRAP-NEO'}) -> CMP-STRAP-NEO: $14.00
  [ok  ] find_approved_policy({'case_id': 'CASE-2001'}) -> no active policies exist ...
structured output  : InvestigationReport
```

One `strands.Agent`, real `@tool` functions, real tool calls, typed Pydantic structured
output. The default model path is:

| `PRECEDENZ_MODEL_PROVIDER` | What it is | Needs |
| --- | --- | --- |
| `mubit` (default) | Strands `OpenAIResponsesModel` via the Mubit gateway (default `gpt-6-luna`) | `MUBIT_API_KEY` |

Regression tests inject a deterministic model fake from `tests/fake_model.py`.
It is not shipped as a runtime provider. Tests still exercise the real Strands
agent loop, schema validation, guardrails, replay, and human activation gate.

`make smoke` uses the configured provider. In Mubit mode it makes hosted model
calls; the default test suite uses test-only fakes. Additional provider
adapters remain available in `app/agent/providers/` and `.env.example`.

Tools run sequentially because they share a database connection. Source-system
reconciliation, not model confidence alone, determines whether a report is usable.

---

## The safety architecture

The separation between *reasoning* and *acting* is the product.

| # | Guarantee | Where it lives |
| --- | --- | --- |
| 1 | The model may propose. It may **never** activate. | `app/policy/store.py::activate` |
| 2 | Deterministic code decides, not the model. Facts are re-derived from the systems and the agent's report is reconciled against them; a disagreement escalates. | `app/facts.py` |
| 3 | Policies are **data**, not code: allowlisted fields, operators, value types, thresholds and actions. | `app/policy/schema.py` |
| 4 | No generated Python, SQL, shell, or natural-language conditions. No `eval` anywhere. | `app/policy/engine.py` |
| 5 | Hard invariants outrank policies. A policy can only ever *narrow* automation. | `app/policy/guardrails.py` |
| 6 | Replay gates activation: one false automatic action blocks it outright, for a human revision exactly as for an agent proposal. | `app/policy/replay.py` |
| 6b | A revision can tighten or loosen a boundary within the guardrails, but has no way to remove a safety condition or add an action. | `app/policy/proposal.py` |
| 7 | Default to escalation on anything ambiguous. | throughout |
| 8 | Every state-changing action carries an idempotency key. | `app/adapters/warehouse.py` |
| 9 | Append-only, hash-chained audit log; `UPDATE`/`DELETE` rejected by database triggers. PostgreSQL also revokes those privileges from `PUBLIC`; a non-owner application role is not set up yet (see `docs/database.md`). | `app/audit.py`, `app/db/` |
| 10 | The decision card's summary is plain language for a supervisor; the output schema asks for no internal reasoning. | `app/domain.py` |
| 11 | A second, independent reader checks the evidence before any action: Banyan Sapling must return `supported`, or the case escalates. It can stop an action, never start one. | `app/sapling.py` |

**The agent has no state-changing tools at all.** Every tool in `app/agent/tools.py` is
read-only. `execute_approved_policy`, `verify_action`, and policy activation are ordinary
Python functions in `app/orchestrator.py` and `app/policy/store.py` that the model has no
way to call.

### Always escalates

Serial mismatch · incomplete evidence · new damage · missing or excessive replacement
cost · serialized, safety-critical, or essential missing component · more than one
missing component · conflicting policies · tool failure · confidence below threshold ·
any disagreement between the agent's report and the source systems · Banyan Sapling
not supporting the action, or Sapling being unreachable.

### Semantic verification with Banyan Sapling

The policy engine reads structured fields only. That keeps it predictable, and it
also means it can't read free text. If an inspector writes "battery door is scorched,
I could not tick the damage box because the form was already submitted", then
`new_damage_present` is still false and the engine would approve the case.

So after a policy matches and before anything is written, the orchestrator sends
[Banyan Sapling](https://huggingface.co/MubitLabs/banyan-sapling) three strings:

- **policy**: the matched policy, rendered as plain language from its conditions
- **evidence**: the raw source-system record, with serials shown side by side (not
  compared) and the inspector's free-text notes verbatim. It never includes the agent's report.
- **claim**: "This case meets every condition of the policy and may be resolved
  automatically."

Only a `supported` verdict with p >= 0.5 lets the action run. Any other result
escalates to a person, with Sapling's label in the reason: `violation`,
`insufficient_information`, `inconsistent_context`, a weak `supported`, or an
unreachable endpoint (the check fails closed). The verdict and all four probabilities
go into the hash-chained audit log.

Measured against the live endpoint:

| Case | Structured fields | Inspector note | Engine alone | Sapling | Outcome |
| --- | --- | --- | --- | --- | --- |
| CASE-2002 | clean | none | approve | `supported` 0.70 | auto-resolved |
| CASE-2009 | clean | "strap was lost on holiday" | approve | `supported` 0.66 | auto-resolved |
| CASE-2007 | clean | "battery door is scorched…" | **approve** | `insufficient_information` | escalated |
| CASE-2008 | clean | "engraved serial underneath reads 4100-SN-09999" | **approve** | `inconsistent_context` | escalated |

All 30 original fixture cases also got the right raw label (30/30), including both
prompt-injection notes. With Sapling enforced, `make eval` still scores 24/24 with no
missed automations.

Sapling's calibrated `status` returns `unsure` on almost every case in this domain, so
the gate uses the raw label and its probability. The calibrated status is still
recorded in the audit log.

Sapling is enforced by default. Regression tests explicitly disable or stub the
endpoint so they do not send case data to a hosted service.

---

## Regression results

The offline regression harness exercises the real orchestration with a test-only
model fake. These results describe application behavior on synthetic fixtures,
not Mubit model accuracy. Full report: [docs/evaluation-results.md](docs/evaluation-results.md).
`make eval` runs the configured hosted model and replaces that report with its
measured results; it uses API credits.

| Metric | Result | Target |
| --- | --- | --- |
| Cases evaluated | 24 fixed synthetic cases | n/a |
| Task-completion rate | 100.0% | high |
| Correct auto-resolution rate | 100.0% | high |
| Correct-escalation rate | 100.0% | 100% |
| **False automatic actions** | **0** | **0** |
| **Prohibited actions** | **0** | **0** |
| **Duplicate actions** | **0** | **0** |

The number that matters is **false automatic actions**: cases the system acted on by
itself that a person should have seen. Zero is a design constraint, not an average.

---

## Beyond returns

The subject is returns because a demo needs one. The governance machinery underneath
serves a second domain as well. Counting non-blank Python lines, the domain packs and their
adapters are about 850 lines against about 5,550 in the rest of `app/`, and each domain's
facts, guardrails, actions and corpus live in its pack under `app/domains/`. The rest is
not fully domain-neutral yet: the agent's tools and prompts, the web templates, and a few
defaults in the policy code (the default policy family, the fields the revision form
offers) still speak returns.

A pack supplies six things and nothing else:

1. the fact record a policy may test, and how those facts are derived,
2. the non-overridable guardrails,
3. the fixed action set, with its caps,
4. an executor for those actions and a read-back verifier,
5. a labeled historical corpus to replay against,
6. the hard ceilings no policy may exceed.

**A second domain ships in this repository.** `ap.invoice_variance` governs an
accounts-payable exception: an invoice that does not match its purchase order. Its facts,
guardrails, actions and systems of record have nothing in common with returns.

| | Returns | Accounts payable |
| --- | --- | --- |
| Family | `returns.missing_accessory` | `ap.invoice_variance` |
| The recurring question | *a kit came back missing its lens cap, what do I do?* | *the invoice is $42 over the PO, do I pay it?* |
| Fields a policy may test | 9 | 9 |
| Hard ceilings | $50 replacement, 1 component | $250 variance, 5% of the PO |
| Fixed actions | hold for parts, raise a replacement work order, close | post a variance adjustment, release for payment, close |
| Guardrails | serial mismatch, new damage, safety-critical or essential part, incomplete evidence | duplicate invoice, vendor on hold, no PO, receipt mismatch, tax mismatch, missing approver |
| Labeled corpus | 24 return cases | 24 invoices |
| Replay of the taught policy | 11 automated, 13 escalated, **0 wrong** | 12 automated, 12 escalated, **0 wrong** |

What the second domain reuses: the constrained policy language, the deterministic engine,
the replay gate, the activation gate and its token, idempotent execution, read-back
verification, the hash-chained audit log, the policy diff, and both database backends.
Making room for it reshaped that code once, in the same commit: the policy schema became
domain-driven, and returns-specific dispatch moved out of the orchestrator into the pack,
which deleted 56 lines there and added 17.

`tests/test_domain_ap_invoices.py` is the evidence: 17 tests that touch no returns code.
They cover fact derivation from the ledger, every
guardrail boundary, a policy language that refuses a field from another domain and an
adjustment cap above its own condition, a replay that scores 24 labeled invoices, an
activation refused without a passing replay and without a token, execution that pays once
when run twice, verification that reads the ledger back, and the audit chain intact.

**What a new domain costs:** about 480 lines of Python (a fact record and its derivation, the
guardrails, the action set, an executor and verifier, a synthetic adapter), plus its
fixtures, its labeled corpus and its tests.

**What it does not include yet, honestly:** the web app's screens and the agent's tools and
prompts are still returns-shaped, so the AP domain is exercised through the governance path
and its tests rather than through the agent loop and the UI. Making a domain first-class in
the interface is UI work on top of the seam, not another rewrite of the machinery.

**Where this design refuses to go:** a domain has to reduce its judgment to allowlisted
fields. Where the decision genuinely turns on free-text nuance that cannot be derived
deterministically, there is nothing to replay and nothing to bound, so this system will
not automate it. That is a constraint by construction, not an omission.

---

## The synthetic world

Everything is invented. The company is **Wharf Canary Optics**, a fictional camera and
optics rental/resale business. All SKUs, serials, orders, customer references, inspection
notes, and warehouse records are fabricated for this demo, and every "business system"
is a labeled synthetic adapter in `app/adapters/` backed by local SQLite.

**No real customer data, no PII, no private SOPs, no credentials, and no production
integrations anywhere in this repository.** The UI says so on every page.

---

## Core layout

```
app/
  agent/           the one Strands agent: tools, prompts, model providers
    providers/     Mubit gateway and optional hosted backends
    tools.py       READ-ONLY tools. Nothing here writes.
  adapters/        SYNTHETIC business systems (returns, parts, warehouse, AP ledger)
  domains/         domain packs: facts, guardrails, actions, corpus
    returns.py     the returns domain
    ap_invoices.py a second domain on the same machinery
  policy/
    schema.py      the allowlist: fields, operators, values, actions
    diff.py        policy-version diff, classified narrower / wider
    guardrails.py  non-overridable invariants
    engine.py      deterministic evaluation, no dynamic execution
    replay.py      the replay gate
    store.py       persistence + THE ACTIVATION GATE
  orchestrator.py  the golden path; the only code that acts
  sapling.py       Banyan Sapling semantic verification client
  facts.py         deterministic facts + reconciliation
  audit.py         append-only, hash-chained
  progress.py      in-memory progress for the waiting panel; never read by anything
                   that decides, acts, or records
  evaluation.py    the measurement harness
  main.py          FastAPI: inbox / decision+replay / policies+audit / dashboard
  dashboard.py     history and usage, counted from case records and the audit log
  static/          app.css and waiting.js (the panel shown during a model call)
  db/
    postgres_backend.py  deployment target: pooling, advisory lock, NUMERIC
    sqlite_backend.py    zero-setup demo backend
    migrations/          versioned SQL, one file per dialect
fixtures/          synthetic catalog + 24 historical + 9 demo return cases + AP corpus
tests/             offline regression suite; supports both database backends
docs/              scope, architecture, database, evaluation, provenance
tools/diagram/     renders docs/architecture.{svg,html} from architecture.json
```

## Database

**PostgreSQL is the deployment target. SQLite is a labeled zero-setup demo
backend**, kept so the product runs from a fresh clone with no server. One
environment variable switches between them. **The same suite supports both
backends**; `make test-pg` exercises PostgreSQL when the configured test server is
reachable.

Porting off SQLite surfaced three defects that were only invisible because
SQLite has a single writer:

1. **The audit hash chain could fork.** Appending is read-head-then-insert, a
   race with concurrent writers. Fixed with a transaction-scoped advisory lock,
   plus `UNIQUE(prev_hash)` as a backstop. Remove the lock and
   `test_concurrent_audit_appends_keep_the_chain_intact` fails immediately with
   a duplicate-key violation on the forked head. The test has teeth.
2. **Event de-duplication was select-then-insert.** Two copies of one warehouse
   event both passed the check. Now `ON CONFLICT (event_key) DO NOTHING
   RETURNING`, so the database decides the race and the loser is a no-op.
3. **Money was floating point.** `REAL` gating an authorized spend cap is a
   latent rounding bug. Now `NUMERIC(12,2)`, coerced at the adapter boundary.

Full write-up, including what still stands between this and production:
[docs/database.md](docs/database.md).

## Configuration

Copy `.env.example` to `.env` if you do not already have one, and set
`MUBIT_API_KEY`. The app reads it on startup. Model access uses Mubit by default;
regression tests do not need a key.

A real environment variable always beats the file, so a deployment cannot be overridden
by a stray `.env` on disk. `.env` is gitignored and a test asserts it stays that way.
**Never commit a real `.env`**. It is where an API key goes.

## Tests

```bash
make test        # hermetic. No network, no model calls. SQLite only.
make test-pg     # the whole suite against BOTH backends; start PostgreSQL with make db-up
pytest -m integration    # opt-in, needs a live model provider
```

Coverage includes: policy-schema validation, allowlisted fields/operators/actions,
default escalation, approval and policy-version checks, threshold boundaries,
conflicting policies, replay gating, action idempotency, duplicate events, append-only
audit behavior and tamper detection, malformed and incomplete evidence, prompt
injection inside case notes, model timeout and tool failure, unknown case → decision
card, approval → candidate without activation, failed replay blocking activation,
explicit activation after successful replay, later matching case completing
automatically, and a risky near-match escalating, plus, on PostgreSQL,
concurrent audit appends, concurrent duplicate events, and concurrent
idempotency-key collisions.

## License

Apache-2.0. See [LICENSE](LICENSE). GitHub detects it, so it shows in the About sidebar.
