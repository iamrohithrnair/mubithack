# Decide Once: Precedenz demo branch plan

Date: Saturday 2026-10-24, 22:10 UTC (23:10 BST).
Status: plan. Nothing in it is built yet.
Branch: `feat/decide-once`, from `main` at `be95704`.
Hard stop: Sunday 2026-10-25, 07:00 UTC (07:00 GMT). London clocks go back at 01:00 UTC.

Related: [meta-harness plan](meta-harness-plan.md), [Mubit research](mubit-research.md),
[hackathon notes](hackathon-notes.md). On this branch, this plan replaces sections 9 to 12
of the meta-harness plan (code changes, build order, tests, demo). Its safety rules still
apply.

## 1. The idea

> **Never answer the same question twice.**
> **It doesn't make decisions. It repeats yours.**
> **If it can't check it, it asks.**

- Line 1 is the promise. Everyone is someone's Dana: the person who gets pinged with the
  same question every week. Shorter forms: "Decide once." "The last time you'll answer that."
- Line 2 removes the fear. Most people won't let AI decide for them. Precedenz can only
  repeat a decision a person already made.
- Line 3 is the safety rule. It works the same way in every domain.

The name comes from precedent: decide a case once, follow it, flag what is different, and
overrule it when it is wrong.

Precedenz is a continual learning loop that any organization, team, or individual can attach
to a recurring decision. A workstream is a contract plus past answers. You plant one from a
template or write a contract; you do not write code. There are two loops, and each belongs
to a different system:

```text
PRECEDENT LOOP (Precedenz): learns from one answer
  question ─► matches a precedent, and every check passes? ─► FOLLOW: repeat the person's answer
                │ no, or something can't be checked
                ▼
              ASK a person ─► the person answers ─► "remember this"
                ─► replay against past answers ─► precedent
                ─► waiting questions it covers resolve on their own
  the person overrules ─► the precedent stops at once; its kind of question asks again

MODEL LOOP (Mubit): learns from many calls
  every reading step goes through the Mubit gateway:
  one stable task per workstream, one session per case
  Mubit keeps examples ─► trains ─► tests ─► silent test ─► partial rollout ─► live
  a promoted model changes who reads the case, never what may be repeated
```

### What the demo proves

| Claim | Shown by |
| --- | --- |
| It fits any workflow | An AI agent (MCP), a terminal (CLI) and a script (HTTP) use one contract |
| Decide once | One answer becomes a precedent, and the waiting questions it covers resolve |
| It can't overreach | A different or uncheckable case asks, even when the model is confident |
| Any domain, any size | A new workstream is planted live from a template, for a team and for one person |
| Two loops | Each workstream is one clean Mubit task, and Mubit's stage shows beside the business results |

## 2. What Precedenz owns, and what it leaves to Mubit

| Concern | Owner |
| --- | --- |
| Recording calls, grouping them into tasks, keeping examples | Mubit |
| Training, testing, silent test, partial rollout, live, audits, rollback | Mubit |
| Which model serves a call | Mubit (same gateway, same model ID) |
| Checking a claim against policy and evidence | Banyan Sapling, called by Precedenz |
| Workstream contract: fields, ceilings, the answer that may be repeated, always-ask rules | Precedenz, defined by the person or team |
| Precedents: draft from an answer, replay, activate with approval, overrule | Precedenz |
| Follow or ask, the receipt, and the Mubit request IDs on it | Precedenz |
| Carrying out the action | The caller's own system |

Not in this branch, because Mubit already does it or it belongs to Mubit:

- choosing or switching model IDs, picking adapter versions, or polling for promotion;
- curating, exporting or labeling training data;
- grading, comparing or scoring models;
- a cost or latency dashboard that repeats the Mubit console.

The lifecycle mirror on the map is display only. It shows the stage the Mubit console
reports for each workstream's task, with its source and time beside it. Nothing is computed
or guessed. Without a console snapshot, it shows nothing.

### Why Mubit will care

- Precedenz turns everyday organizational work into the traffic adapters need: one stable
  task per workstream, a genuine session per case, and many repeats by design. Customers
  never have to learn what a task fingerprint is.
- It guards the fingerprint. Each workstream's task key (model, instructions, tools, output
  format) is computed locally, and a change is flagged before a prompt edit strands the
  examples already collected.
- It makes automatic promotion an easy yes for risk teams: a promoted model can change who
  reads the case, never what may be repeated.
- Banyan Sapling runs in a real loop, as the "can we check it" gate before a follow that
  has evidence.
- Every business decision carries its `x-middleware-request-id` values, so support can go
  from a receipt straight to the console.
- It opens institutions that will not let AI decide, because the AI never decides.

## 3. Words in the product and in the code

| Product word | Existing code | Notes |
| --- | --- | --- |
| Workstream | policy family, `DomainSpec` | `returns.missing_accessory`, `ap.invoice_variance`, and planted ones |
| Precedent | policy (`policies` table) | One active per workstream, as the activation gate already enforces |
| Follow | `auto_resolve` | Repeat the precedent's answer; the caller executes it |
| Ask | `escalate` | With reasons: what differs, or what couldn't be checked |
| Remember (decide once) | candidate, replay, activate | Needs the approval token |
| Overrule | retire | New `store.retire()`; nothing replaces the precedent |

## 4. Architecture

```text
 Callers                           Precedenz                                          Mubit
 ───────                           ─────────                                          ─────
 AI agent ── MCP (ask only) ──┐
 Terminal ── precedenz CLI ───┼─► /v1 API ─► decision service ─► engine for the workstream
 Pipeline ── HTTP ────────────┘     │          │                  ├─ returns: agent reads ───────► gateway
                                    │          │                  │  facts re-derived, reconciled
 Person ── map or CLI answer ───────┤          │                  ├─ AP: rules only, no model call
   (approval token)                 │          │                  └─ planted: caller facts and/or
                                    │          │                     the reader ─────────────────► gateway
                                    │          ├─ hard rules ─► precedent match ─► Sapling ──────► Banyan Sapling
                                    │          └─ receipt (never changes), audit entry, event
                                    └─► /map (live over SSE)
 Caller's executor ◄── follow: bounded actions, expiry, source revision
```

| Workstream | Facts come from | Model step through Mubit | Checks before a follow |
| --- | --- | --- | --- |
| Returns (built in) | Re-derived from the returns system by `case_ref` | The existing investigation agent and its read-only tools; task shape unchanged | Reconciliation, hard guardrails, precedent match, minimum confidence, Sapling |
| AP invoices (built in) | Re-derived from the AP ledger | None | Hard guardrails, precedent match, Sapling where the domain renders text |
| Planted | The caller's typed facts, the reader's reading of the text, or both | The reader: one structured-output call per case | Required fields, ceilings, always-ask rules, reconciliation when both facts and text arrive, precedent match, Sapling when the contract requires evidence |

Safety invariants. Each one gets a test.

- The decision path never calls a domain's `execute` or `verify`, or any adapter write.
- A follow needs a known workstream, an active precedent that matches, and every check
  passed. Any failure, outage, timeout or missing field returns an ask with the reason.
- Actions come from the precedent and the workstream's `ActionSpec` caps, never from model
  output.
- Only a person with the approval token can plant a workstream, remember an answer as a
  precedent, or overrule one. The MCP server has no tool for any of these. Your agent can
  ask; only you can answer.
- A precedent activates only after replay against past answers finds zero contradictions.
- Receipts never change. Answers and outcomes are appended.
- The same idempotency key with the same input returns the first decision; with a different
  input it returns 409.
- Serving identity reads "not reported by the gateway" unless Mubit documents a field for it.
- Model calls send `metadata.session_id` as `<workstream>:<case_ref>`, and keep each
  workstream's instructions, tools, tool choice and output format stable.

## 5. Interfaces frozen in Phase 0

The lead writes these before any agent starts. An agent that needs a change reports it and
does not edit a shared file.

### 5.1 HTTP API

Every `/v1` route takes `Authorization: Bearer $PRECEDENZ_API_TOKEN`. Person-only routes
also take `X-Approval-Token`.

| Route | Used by | Purpose | Owner |
| --- | --- | --- | --- |
| `POST /v1/decide` | caller | Follow or ask for one case | A |
| `GET /v1/decisions/{id}` | caller | Receipt, answers, outcomes, `resolved_by` | A |
| `GET /v1/decisions?verdict=ask&open=true` | caller | Open questions | A |
| `POST /v1/decisions/{id}/outcome` | caller | Report executed or failed | A |
| `POST /v1/decisions/{id}/answer` | person | Answer an ask, and optionally remember it | C |
| `POST /v1/precedents/{id}/overrule` | person | Stop a precedent | C |
| `GET /v1/workstreams`, `GET /v1/templates` | caller | What exists, and what can be planted | C |
| `POST /v1/workstreams` | person | Plant from a template or a contract | C |

`POST /v1/decide`:

```json
{
  "workstream": "expenses.meal_claim",
  "case_ref": "EXP-1042",
  "idempotency_key": "evt-88121",
  "facts": {"amount_usd": 62.4, "receipt_attached": true, "alcohol_only": false},
  "text": "Lunch with the Northwind buyer, receipt attached, $62.40",
  "source_revision": "r3"
}
```

`facts` and `text` apply only to planted workstreams. Built-in workstreams take `case_ref`
and re-derive everything from their source systems.

Response:

```json
{
  "decision_id": "dec_01J…",
  "verdict": "follow",
  "workstream": "expenses.meal_claim",
  "case_ref": "EXP-1042",
  "precedent": {"id": "pol_…", "version": 1,
                "text": "Approve meal claims up to $75 with a receipt, unless alcohol only.",
                "decided_by": "dana.r@wharf-canary-optics.example", "decided_at": "…",
                "origin_case": "EXP-1031"},
  "actions": [{"type": "answer", "answer": "approve", "max_amount_usd": 75.0}],
  "reasons": [],
  "checks": [{"name": "required fields present", "ok": true},
             {"name": "always-ask rules", "ok": true},
             {"name": "matches precedent v1", "ok": true},
             {"name": "Banyan Sapling: evidence supports it", "ok": true, "detail": "supported, p=0.71"}],
  "model_calls": [{"via": "mubit", "request_id": "mreq_…", "requested_model": "gpt-6-luna", "ms": 2140}],
  "serving_identity": "not reported by the gateway",
  "task_key": "7f3a9c…",
  "input_hash": "sha256:…",
  "source_revision": "r3",
  "created_at": "…",
  "expires_at": "…"
}
```

An ask has `"precedent": null` or the nearest precedent, empty `actions`, and reasons such
as `"amount_usd 140.00 is above the precedent's 75.00"` or `"Banyan Sapling could not
confirm the evidence (insufficient information)"`.

Errors: 401 for a bad token, 403 for a missing approval token, 404 for an unknown workstream
or decision, 409 for an idempotency conflict, 422 for a malformed body. A model, Sapling or
tool failure is never an error. It is an ask.

### 5.2 Workstream contract (`app/workstreams/contract.py`)

```python
class FieldSpec(BaseModel):
    name: str                            # snake_case, stable
    kind: Literal["flag", "category", "amount", "count"]
    label: str
    values: list[str] | None = None      # category only
    ceiling: float | None = None         # amount and count: no precedent may exceed it
    required: bool = True

class WorkstreamContract(BaseModel):
    id: str                              # "expenses.meal_claim"
    label: str                           # "Meal expense claims"
    subject: str                         # "meal claim"
    question: str                        # "Should this meal claim be approved?"
    owner: str                           # the team or person who answers
    audience: Literal["team", "individual"]
    fields: list[FieldSpec]
    repeatable_answer: str               # the one answer a precedent may repeat, e.g. "approve"
    other_answers: list[str]             # answers a person may give that are never repeated
    always_ask: list[Condition]          # the existing policy condition model
    reader: bool                         # the model reads `text` into fields, through Mubit
    evidence: Literal["none", "text"]    # "text": Sapling must confirm before a follow
    version: int = 1
```

Built-in workstreams keep their `DomainSpec`. They gain an optional `repeatable_answer`
label (returns: "replace the missing part"; AP: "approve the variance") and their existing
`fixed_actions` stay the action set.

### 5.3 Drafting a precedent from an answer

Deterministic, with no model. The precedent is exactly what the person decided, made wider
only where the person says so. It works for every workstream, built in or planted; field
kinds come from the contract, or from the `DomainSpec` field sets (bool is a flag, enum is a
category, number is an amount).

- Flag and category fields: `eq` the observed value.
- Amount fields: `lte` the larger of the observed value and the person's `up_to`.
- Count fields: `eq` the observed value, unless the person gives a range.
- Fields in `ignore` are dropped ("doesn't matter").
- Always-ask rules stay hard rules and are never part of a precedent.
- Actions: `spec.fixed_actions(cap)`. For a planted workstream that is
  `[{"type": "answer", "answer": <repeatable_answer>, "max_<amount>": cap}]`.

Replay runs against the workstream's past answers. For a planted workstream, a past case is
labeled `auto_resolve` when its answer equals the repeatable answer, and `escalate`
otherwise; built-in workstreams use their existing labeled corpus. Replay uses
`enforce_coverage=False`, because a person may automate as little as they like, and it
still needs zero false automatic actions.

After activation, the open asks in that workstream are assessed again, at most 20 per
remember. Planted workstreams reuse the stored, already-reconciled facts, so no new reader
call is made; Sapling still runs. Built-in workstreams re-derive from the source. A case that
now matches gets a new follow decision with `resolves: <ask id>`. The asks themselves are
never edited.

### 5.4 Engines (`app/harness/engines.py`)

```python
@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""

@dataclass(frozen=True)
class Assessment:
    verdict: Literal["follow", "ask"]
    reasons: tuple[str, ...]
    checks: tuple[Check, ...]
    precedent_id: str | None
    precedent_version: int | None
    actions: tuple[dict, ...]
    facts: dict[str, object]

class Engine(Protocol):
    family: str
    def assess(self, conn, request: DecideRequest, *, trace_id: str, calls: CallLog) -> Assessment: ...

def register_engine(engine: Engine) -> None: ...
def engine_for(family: str) -> Engine: ...     # KeyError becomes 404
```

### 5.5 Model wiring (`app/agent/providers`, `app/agent/tools.py`)

```python
@dataclass
class ModelCall:
    request_id: str | None     # x-middleware-request-id; None when the header is absent
    requested_model: str
    status: int
    ms: int

class CallLog(list[ModelCall]):
    family: str
    case_ref: str
    session_id: str

def resolve_model(settings=None, *, call_log: CallLog | None = None, **overrides) -> Model: ...
```

`AgentContext` gains `call_log: CallLog | None = None`, and `build_agent` passes it to
`resolve_model`. The model fake hook in `tests/conftest.py` becomes
`lambda *a, **k: DeterministicTestModel()`.

### 5.6 Events (`app/harness/events.py`)

In process, bounded and thread-safe, read by the SSE route with `Last-Event-ID`. Losing an
event costs an animation and nothing else: nothing that decides reads this bus. It lives in
one process, like `app/progress.py`, so the server runs with one worker.

| Type | Fields besides `seq`, `at`, `type` |
| --- | --- |
| `decision.started` | workstream, case_ref, caller |
| `model.call` | workstream, case_ref, request_id, requested_model, ms |
| `decision.finished` | decision_id, workstream, case_ref, verdict, precedent_id, reasons, resolves, caller |
| `precedent.decided` | precedent_id, workstream, version, decided_by, origin_case, text |
| `precedent.overruled` | precedent_id, workstream, by, reason |
| `workstream.planted` | workstream, label, audience |
| `case.answered` | decision_id, answer, remembered |
| `outcome.reported` | decision_id, status |

```json
{"seq": 41, "at": "2026-10-25T01:14:07Z", "type": "decision.finished",
 "decision_id": "dec_…", "workstream": "returns.missing_accessory", "case_ref": "CASE-2003",
 "verdict": "ask", "precedent_id": null, "reasons": ["serial does not match"],
 "resolves": null, "caller": "agent"}
```

### 5.7 Map snapshot (`GET /map/data`)

```json
{
  "generated_at": "…", "last_seq": 41,
  "people": [{"id": "dana.r@wharf-canary-optics.example", "label": "Dana R."}],
  "workstreams": [{
    "id": "returns.missing_accessory", "label": "Returns · missing accessory",
    "kind": "built-in", "audience": "team", "reader": true,
    "task_key": "7f3a9c…", "task_key_changed": false,
    "mubit": {"stage": "Collecting examples", "detail": "18 of 25 examples",
              "source": "Mubit console", "as_of": "…"},
    "precedents": [{"id": "pol_…", "version": 1, "status": "active", "text": "…",
                    "decided_by": "…", "decided_at": "…", "origin_case": "CASE-2001",
                    "cases": [{"case_ref": "CASE-2002", "decision_id": "dec_…",
                               "verdict": "follow", "at": "…", "via": "api"}]}],
    "waiting": [{"case_ref": "CASE-2003", "decision_id": "dec_…", "reasons": ["…"], "at": "…"}],
    "counts": {"follow": 12, "ask": 3, "answered": 1, "model_calls": 41}
  }],
  "templates": [{"id": "calendar.meeting_invite", "label": "Meeting invites", "audience": "individual"}],
  "totals": {"answered_once": 2, "repeated": 31, "asked": 9, "model_calls": 64}
}
```

`mubit` is `null` when there is no console snapshot. Cases decided in the existing inbox
appear with `"via": "inbox"`. The map's read routes follow the app's existing page model
(the local demo has no login); its write routes take the approval token, like policy
activation does today.

### 5.8 Storage and configuration

Migration `0004_harness`, one file each for SQLite and PostgreSQL:

| Table | Columns |
| --- | --- |
| `workstreams` | `id` PK, `contract` JSON, `version`, `audience`, `planted_by`, `planted_at` |
| `harness_cases` | (`workstream`, `case_ref`) PK, `facts` JSON, `text`, `source`, `revision`, `received_at` |
| `harness_decisions` | `decision_id` PK, `workstream`, `case_ref`, `idempotency_key`, `input_hash`, `verdict`, `precedent_id`, `precedent_version`, `actions`, `reasons`, `checks`, `model_calls`, `task_key`, `caller`, `trace_id`, `resolves`, `created_at`, `expires_at`; UNIQUE (`workstream`, `idempotency_key`) |
| `harness_answers` | `answer_id` PK, `decision_id`, `answer`, `remembered`, `precedent_id`, `actor`, `note`, `created_at` |
| `harness_outcomes` | `outcome_id` PK, `decision_id`, `status`, `detail`, `actor`, `verification` (`reported`), `created_at` |

New settings: `PRECEDENZ_API_TOKEN`, shown on screen only while the default is in use, like
the approval token; and `PRECEDENZ_MUBIT_CONSOLE_SNAPSHOT`, default
`var/mubit_console.json`. This stays a local demo: never deploy it publicly with default
tokens.

## 6. The map: Neural Bioluminescence

Route `/map`, with a nav item "Map". The page is full-bleed and dark only: bioluminescence
needs the dark, so this one page commits to a single look and ignores the theme toggle. The
rest of the app keeps its own design system. Fonts are the app's Geist Sans and Geist Mono.
The only library is the vendored Motion 11.18.2 (`window.Motion`) plus the existing
`motion.js` helpers. No new library, no build step. `map.js` computes the radial cluster
layout itself.

The rule: **every pulse is a real decision.** No idle sparks and no decorative traffic.
Ambient motion is limited to breathing light and drifting plankton.

### 6.1 What each element means

| Element | Meaning | Data |
| --- | --- | --- |
| Center soma | The people who decide (Dana) | `people` |
| Ring 1 nodes | Workstreams, one hue each, in planting order | `workstreams` |
| Ring 2 nodes | Precedents. Retired versions stay, dimmed, with the cases they decided | `precedents` |
| Ring 2 amber node | "Waiting for a person" in that workstream | `waiting` |
| Rim leaves | Cases. Workstream hue: followed. Amber: asked. White: answered by a person. Slate: decided by an overruled precedent | `cases`, `waiting` |
| Leaf afterglow | Recent decisions glow brighter and fade over 20 seconds | `at` |
| Outer arc per sector | The Mubit loop for that workstream's reading task: the stage segment lit, with its source and time | `mubit` |
| No arc | No model step (AP: rules only) | `reader: false` |

### 6.2 Tokens

```css
:root {
  /* Layout: radial cluster. People at the center, cases on the rim, HUD in the corners. */
  --abyss: #02050b;            /* edge of the ground; the center glows #061226 */
  --plankton: #7fdcff;
  --ink: #d6ecff;
  --ink-dim: #8aa4bf;
  --ink-faint: #4c6178;
  --hue-1: #42e8ff;
  --hue-2: #4dffc0;
  --hue-3: #a98bff;
  --hue-4: #ff6bd6;
  --hue-5: #c4ff6b;
  --ask: #ffb547;              /* one warm light among cool ones: readable at a glance */
  --decide: #f4fbff;           /* the white-hot birth of a precedent */
  --overrule: #ff5a6e;         /* a flash, then the subtree fades to --dormant */
  --dormant: #2a3550;
  --mubit: #7c7bff;            /* the model loop */
  --glass: rgba(6, 14, 28, .55);
  --glass-line: rgba(120, 200, 255, .12);
  --font-ui: "Geist Sans", system-ui, sans-serif;
  --font-data: "Geist Mono", ui-monospace, monospace;
}
```

### 6.3 Motion

| Moment | Choreography |
| --- | --- |
| Load: the bloom, at most 2.5 s | The soma ignites (spring). Workstream links draw out (`pathLength` 0 to 1, 0.08 s stagger). Nodes pop, precedent links draw, leaves ripple in by angle, Mubit arcs sweep. The final frame is complete at rest. |
| Ambient | Nodes breathe (opacity 0.7 to 1, 3 to 6 s, random phase). About 120 plankton particles drift with depth parallax on a canvas, paused when the tab is hidden. |
| `decision.started` | A spark lands on the rim of the sector. The branch shimmers violet while the reader works. |
| `model.call` | A violet spark runs along the sector's Mubit arc, one per gateway request ID. |
| Follow | A comet travels rim, workstream, precedent. The precedent flares and a new leaf ignites in the workstream hue. |
| Ask | An amber comet travels all the way to the soma. An amber ripple, and the waiting node grows. |
| `precedent.decided` | A white shockwave from the soma. A new dendrite grows to ring 2 and blooms into the workstream hue. Matching waiting leaves fly from the waiting node to the new precedent one by one. This is the "decide once" moment; give it the most care. |
| `precedent.overruled` | A coral flash; the subtree desaturates to dormant over 1.2 s and its link turns dashed. |
| `workstream.planted` | Sectors rotate apart (spring relayout) to open a new one, and a new branch grows. |

Comets draw on one canvas overlay (`globalCompositeOperation = "lighter"`) and follow SVG
paths through `getPointAtLength`. SVG holds the structure. Use one blur filter per layer
group, never one per leaf.

### 6.4 Heads-up display

- Top left: the wordmark and "Never answer the same question twice."
- Top right: four counters that count up: Answered once, Repeated, Asked, Model calls via Mubit.
- Bottom left: the legend (Follow, Ask, Decide, Overrule, Mubit loop) and "Every pulse is a
  real decision."
- Bottom right: the last five events in mono, such as
  `01:14:07 FOLLOW CASE-2002 ← precedent v1 (Dana, 00:58)`.
- Drawer, opened by click or Enter on a node: the receipt (verdict, precedent text, checks,
  Mubit request IDs, task key, serving identity). On a waiting case: an answer form (answer,
  remember, `up_to`, fields to ignore, approval token). On a precedent: Overrule, with a reason.
- A small "Synthetic demo data" tag, matching the app's banner.
- `?present=1` hides everything except the counters and the legend, for recording.
  `?fixture=1` loads the fixture. `?simulate=1`, for development only, plays a scripted
  event stream under a visible SIMULATED badge, and is never used in the video.

### 6.5 Quality bar

- 60 fps on a laptop at 1920×1080 with 400 leaves.
- With `prefers-reduced-motion`: the static final layout, counters still update, no comets.
- Keyboard: Tab through workstreams and precedents, Enter opens the drawer, Esc closes it.
  An `aria-live` region announces each finished decision. A screen-reader table lists
  workstreams, precedents and counts.
- At phone width, the map fits the screen, the HUD stacks, and the drawer becomes a bottom sheet.

## 7. The team

The lead (this session) writes Phase 0, merges, runs integration, and prepares the demo.
Five agents build in parallel. Each runs in the background in its own worktree made from
the Phase 0 commit (Agent tool, `isolation: "worktree"`), and all five are launched in one
message. Each prompt points the agent at this file (sections 4, 5 and its brief) and at the
code it must read first.

| Agent | Scope | Owns (only these files) | Time box |
| --- | --- | --- | --- |
| A · Harness core | Built-in engines, decision service, store, decisions API, events from the inbox path | `app/orchestrator.py`, `app/harness/service.py`, `app/harness/store.py`, `app/harness/builtin.py`, `app/api/decisions.py`, `tests/test_harness_*.py` | 3 h |
| B · Mubit wiring | Request IDs, sessions, task key, `model.call` events, console mirror | `app/agent/providers/__init__.py`, `app/agent/build.py`, `app/mubit.py`, `tests/test_mubit_wiring.py` | 2 h |
| C · Workstreams | Contracts, registration, reader, answer and remember, overrule, templates, workstream API | `app/workstreams/**`, `app/api/workstreams.py`, `app/domains/**`, `app/policy/store.py`, `fixtures/workstreams/**`, `tests/test_workstreams_*.py` | 3 h |
| D · The map | Everything in section 6 | `app/templates/map.html`, `app/static/pages/map.css`, `app/static/pages/map.js`, `app/static/pages/map.fixture.json`, `tests/test_map_page.py` | 3 h |
| E · Callers and map data | Python client, CLI, MCP server, traffic script, map snapshot and SSE routes, integration guide | `precedenz_client/**`, `scripts/demo_traffic.py`, `app/harness/map_data.py`, `app/api/map.py`, `docs/integrations.md`, `tests/test_clients.py`, `tests/test_map_data.py` | 3 h |

### Rules for every agent

- Edit only the files you own. Code against section 5. If an interface is wrong, stop and
  report it; do not change a shared file.
- Match the surrounding code: docstrings that say why, small functions, the existing test
  style. Read `app/domains/__init__.py`, `app/orchestrator.py` and `tests/conftest.py` first.
- No hosted calls in tests. Use the existing test-only model fake and stub Sapling, as the
  current suite does.
- Never print or store the Mubit key. Never write to a business system from the decision path.
- Run `make test` before reporting. Report failures with their output.
- Commit in your worktree with a clear message. Report what works, what was tested, what is
  not done, and any interface problem.

### Agent briefs

**A · Harness core**

1. Extract `assess_case()` from `_handle_event`: everything from the investigation through
   Sapling, returning an `Assessment`. `_handle_event` calls it, then executes exactly as
   today. The audit event sequence must not change; the existing suite is the proof.
2. Returns engine: wraps `assess_case` and creates no exception row. With no active
   precedent it asks: "no precedent yet: a person decides once". It passes a `CallLog`
   through `AgentContext`.
3. AP engine: facts from the AP ledger, AP guardrails, precedent match, and Sapling where
   the domain renders text. No model call.
4. Service: reserve under the unique key and compare input hashes; hold a process-wide
   decision lock (SQLite allows one writer, and a decision holds it across the model call);
   finalize the receipt and audit entry; publish `decision.started` and
   `decision.finished`. Any exception becomes an ask with the reason.
5. The decisions routes in 5.1, with bearer auth.
6. Publish events from the inbox path too: case resolved, case escalated, policy activated.
7. Tests: a follow (CASE-2002 after activating the demo policy); asks for CASE-2003,
   CASE-2004, CASE-2006, and CASE-2007 with Sapling stubbed; 401, 404, 409; an idempotent
   repeat; the no-write test, which patches every `execute`, `verify` and adapter write to
   raise; a Sapling outage asking when Sapling is enforced.

**B · Mubit wiring**

1. Record `x-middleware-request-id` and the status of every HTTP response. Override
   `_resolve_client_args` on a subclass of `OpenAIResponsesModel` to hand each request a
   fresh `httpx.AsyncClient` with a response event hook. Bind the `CallLog` when the model
   is built; no context variables across threads. Confirm Strands closes the client, and
   that retries and streaming record their own IDs.
2. Send `metadata.session_id`, the field Mubit's docs name for the Responses API. Do not
   change instructions, tools, tool choice, output format or model ID.
3. Task key: SHA-256 over the model ID, system prompt, sorted tool specs and output schema,
   computed at agent build. Put it on receipts. When a workstream's key changes, record an
   audit note and set the map flag.
4. Publish `model.call` events.
5. Console mirror: read the operator's snapshot (per workstream: stage, detail, as_of).
   Accept only Mubit's own stage words (Collecting examples, Training, Passed, Below target,
   Prompt only, Not tested, Silent test, Partial rollout, Live). Return `None` when the
   snapshot is absent. Infer nothing.
6. Tests with `httpx.MockTransport`: IDs recorded per call, session metadata in the request
   body, no key in logs or receipts, a stable task key that changes when the prompt does,
   and the fake model path unchanged.

**C · Workstreams**

1. Contract validation per 5.2.
2. Build a `DomainSpec` from a contract: allowed fields;
   `ActionSpec("answer", literals={"answer": (repeatable,)}, caps, bound_by)`;
   `derive_facts` reading `harness_cases`; `corpus` from past answers; guardrails from
   required fields, ceilings and always-ask rules; `execute` and `verify` that raise ("the
   caller executes"); `policy_text` and `evidence_text` for Sapling. Register workstreams
   at startup and when planted (add an upsert to the registry). Add `repeatable_answer` to
   the built-in specs.
3. Planted engine: facts, reader, reconciliation, guards, match, Sapling, `Assessment`.
4. Reader: one Strands agent per call, a stable system prompt per workstream, an output
   model generated from the contract in field order, no tools, session
   `<workstream>:<case_ref>`.
5. Answer and remember per 5.3, including the re-assessment of open asks after activation.
6. Overrule: `store.retire()`, an audit entry, an event. Later cases ask.
7. Templates, each with 12 synthetic past answers and 20 incoming examples (text and
   facts): `expenses.meal_claim` first, then `calendar.meeting_invite` (for one person),
   then `it.access_request`.
8. The workstream routes in 5.1. Tests: bad contracts rejected; drafts match 5.3; a
   contradiction in history blocks activation; no approval token gives 403; an overrule
   makes the next case ask; a missing field, a ceiling, an always-ask rule, a reader
   mismatch, or an unsure Sapling each ask; waiting asks resolve after remember.

**D · The map**

Build section 6 against `map.fixture.json` first, then against `/map/data` and
`/map/events` once E's routes merge. Keep the page complete at rest. Spend the most care on
the "decide once" moment. Test that `/map` renders and loads its assets.

**E · Callers and map data**

1. `precedenz_client`: a small typed client on `httpx`, which already ships with `openai`.
2. The `precedenz` CLI: `decide`, `receipt`, `open`, `answer`, `overrule`, `plant`,
   `workstreams`, and `watch` (a colored event tail). It reads `PRECEDENZ_URL`,
   `PRECEDENZ_API_TOKEN` and `PRECEDENZ_APPROVAL_TOKEN`.
3. The `precedenz-mcp` server on the installed `mcp` 1.30.0 (FastMCP, stdio): `decide`,
   `get_receipt`, `list_open_questions`, `list_workstreams`. A test asserts that no tool
   answers, remembers, overrules or plants.
4. `scripts/demo_traffic.py`: sequential by default, with `--pace`, `--caller`, and
   `--max-model-calls`, which stops once the receipts reach the budget. Sources: the returns
   demo cases, the AP demo invoices, and the templates' incoming examples.
5. `map_data.py` and the routes `/map/data`, `/map/events` (SSE with `Last-Event-ID`),
   `/map/answer/{id}` and `/map/overrule/{id}`, which call C's services with the typed
   approval token.
6. `docs/integrations.md`: curl, Python, the CLI, MCP for Claude Code
   (`claude mcp add precedenz -- precedenz-mcp`) and Claude Desktop, and a Strands tool
   snippet.

### Phase 0 (lead)

1. `git switch -c feat/decide-once`, then commit the three reference docs and this plan.
2. Write section 5 as code: `app/harness/{__init__,engines,events}.py`; the models in
   `app/workstreams/contract.py`; `CallLog`, `ModelCall` and the `resolve_model` signature;
   `AgentContext.call_log`; migration `0004_harness` for both databases; the new settings;
   router stubs in `app/api/{decisions,workstreams,map}.py` that return 501, mounted in
   `app/main.py` along with a `/map` page stub; the Map nav link in `base.html`;
   `pyproject.toml` (package `precedenz_client*`, scripts `precedenz` and `precedenz-mcp`);
   the conftest hook; and a hand-written `map.fixture.json` matching 5.7 that covers every
   state (active and retired precedents, waiting cases, a workstream with and without a
   Mubit snapshot, AP without a reader).
3. Reinstall the package (`.venv/bin/pip install -e .`), get `make test` green, commit, and
   launch A to E in one message.

## 8. Schedule

Times are UTC. London is on BST until 01:00 UTC and on GMT after it, so local 01:00 to
02:00 happens twice. Schedule by UTC.

| UTC | London | Phase |
| --- | --- | --- |
| 22:15 to 23:00 | 23:15 to 00:00 BST | Phase 0: skeleton, commit, launch the agents |
| 23:00 to 02:00 | 00:00 BST to 02:00 GMT | Phase 1: the agents build. At 00:30 UTC the lead reads each worktree's commits and test status, and answers any interface question |
| 02:00 to 03:00 | 02:00 to 03:00 | Phase 2: merge, test, independent review |
| 03:00 to 04:00 | 03:00 to 04:00 | Phase 3: live run through Mubit and Sapling within budget; seed the demo state; visual polish |
| 04:00 | 04:00 | Feature freeze: bug fixes only; push the branch |
| 04:00 to 05:30 | 04:00 to 05:30 | Phase 4: rehearse, record, export MP4, check that it plays |
| 05:30 to 06:45 | 05:30 to 06:45 | Buffer, merge to `main` if agreed, final push, upload |
| 07:00 | 07:00 GMT | Hard stop |

## 9. Integration and verification

- Merge in this order: B (smallest, done first), A, C, E, D. C needs A's service for the
  re-assessment; E needs C's services for the answer routes; D needs E's routes.
- Run `make test` after each merge. Run `make test-pg` when Docker is available; otherwise
  report the skip.
- Review: a fresh agent reads the merged diff against the invariants in section 4, not
  against a summary of them, and reports findings by severity. Fix what it confirms.
- End-to-end check on a clean seed, through the CLI with the map open: plant
  `expenses.meal_claim`, send five claims (all ask), answer one and remember it, watch the
  waiting claims resolve, overrule, and watch the next claim ask.
- Live budget for the whole night: at most 150 Mubit gateway requests and 60 Sapling calls.
  The traffic script enforces its share. Record request IDs, counts and timings in
  `var/live-run.md`, and quote numbers in the video only from that file.

## 10. Demo video

About three minutes at 1920×1080: the map in presentation mode on the left two thirds, a
terminal and Claude Code on the right. Everything on screen is real behavior.

| Time | Screen | What happens | Voice-over |
| --- | --- | --- | --- |
| 0:00 | Map | The bloom. Dana at the center, the returns and AP branches lit | "Everyone is someone's Dana: the person who gets the same question every week." |
| 0:20 | Claude Code and map | An agent with the Precedenz MCP works the returns queue. CASE-2002 follows Dana's precedent. CASE-2003 asks: the serial does not match. The receipt drawer shows the checks, Sapling's verdict and the Mubit request IDs | "It doesn't make decisions. It repeats yours. If it can't check it, it asks." |
| 0:55 | Terminal and map | `precedenz plant expenses.meal_claim`: a new branch grows. The traffic script sends claims; violet sparks run along the Mubit arc while the reader works; every claim asks, because there is no precedent yet | "Any recurring decision. Plant it with one command." |
| 1:20 | Map drawer | Dana answers one: approve, up to $75, client meeting doesn't matter, remember. Replay: no contradictions in 12 past answers. A precedent is born, and the waiting claims fly to it and resolve | "Decide once." |
| 1:45 | Map | New claims follow. An alcohol-only claim asks (a hard rule). A $140 claim asks: the amount is above $75 | "Anything different goes back to a person." |
| 2:05 | Terminal and map | `precedenz plant calendar.meeting_invite`: Dana's own calendar. One answer: decline external meetings that have no agenda. The next invites follow | "For a finance team, or just for you." |
| 2:25 | Map | Dana overrules the expense precedent. The branch dims, and the next claim asks | "Wrong? Overrule it. It stops at once." |
| 2:40 | Map, zoomed out | Mubit arcs with the console stage and its time; the counters | "Precedenz learns from one answer. Mubit learns from every repeat. When Mubit ships a better model, nothing in your pipeline changes, and its authority doesn't grow." |
| 2:55 | Title | The three lines | "Never answer the same question twice." |

Pre-flight: `make seed`; teach the returns precedent in the inbox (CASE-2001: approve,
activate); warm Sapling with two calls; write `var/mubit_console.json` from the console;
start the server with one worker; open `/map?present=1`; set the environment variables in
the terminal; add the MCP server to Claude Code. Rehearse the whole script once on a fresh
seed, then reset. If model latency makes a scene slow, cut the waiting in editing and say so
on screen.

## 11. If time runs short

Cut in this order:

1. The `it.access_request` template, then `calendar.meeting_invite` (keep expenses).
2. The AP engine.
3. The Mubit console mirror (keep request IDs and task keys on receipts).
4. The answer form in the map drawer (answer from the CLI instead).
5. `precedenz watch` and the extra snippets in the integration guide.
6. The MCP server (keep the CLI and HTTP).

Never cut: the no-write boundary, correct follows and asks, the checks, idempotency, the
approval token on every person-only route, honest labels (synthetic data, serving identity,
the console source and time), passing tests, and the video.

## 12. Risks

| Risk | Mitigation |
| --- | --- |
| Extracting `assess_case` changes inbox behavior | The audit sequence stays identical; the existing suite runs before and after; only A edits the orchestrator |
| SQLite holds the writer lock across a model call, so parallel callers hit `database is locked` after 15 s | A process-wide decision lock; the traffic script runs sequentially |
| The request-ID hook misses calls made on Strands' own thread or event loop | Bind the log per model instance; the MockTransport test covers retries and streaming |
| The Sapling endpoint is cold or down, so everything asks | Warm it before recording. An outage that asks is correct, and the receipt says why |
| Model latency slows the video | The `decision.started` shimmer keeps the map alive; trim waits in editing and say so |
| Merge conflicts | Strict file ownership; shared files change only in Phase 0 |
| A new workstream has no history, so replay proves nothing | Templates ship 12 past answers, and the receipt states how many were replayed |
| An interface gap appears mid-build | The agent reports it; the lead decides at the 00:30 UTC check |
| It is an overnight build | A fixed cut order and a feature freeze at 04:00 UTC |

## 13. Only the user can do these

- Keep `MUBIT_API_KEY` in the environment, and approve the live budget.
- Read the task stages from the Mubit console and write `var/mubit_console.json` before
  recording.
- Record and voice the video, export it, and check that it plays.
- Decide whether to merge `feat/decide-once` into `main` before the stop.

## 14. Questions worth asking Mubit

- Is there a read-only task status API, so the lifecycle mirror can stop being manual?
- Which response field, if any, names the version that served a call?
- Is the Responses route with tools and structured output eligible for automatic serving?
- Is there a supported way to send a person's correction as a label, and does
  `feedback.outcome=off` affect it?
