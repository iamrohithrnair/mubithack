# Precedenz: integration-first meta-harness

Date: 2026-10-24.
Status: implementation plan, not a description of shipped capabilities.
Starting point: commit `be95704`.

## 1. Product decision

Build a small decision-step harness that fits into an existing customer pipeline.
Do not build another workflow platform, model trainer, or model-routing engine.

**Promise:** keep your pipeline; let recurring decisions use Mubit as it learns,
without giving a new model more authority.

The customer keeps its source systems, workflow, human approvals, and executor.
Precedenz supplies a typed decision boundary, deterministic checks, governed
policies, outcome records, and a receipt. Mubit supplies capture, task grouping,
training, model evaluation, and native model promotion where supported.

"Any pipeline" means a pipeline that can call HTTP and supply a bounded task
contract. It does not mean zero-configuration support for every business domain,
or a transparent replacement for every LLM SDK.

## 2. What is verified

### Repository

- Mubit is the default hosted provider, using the OpenAI Responses API.
- The returns app already has reconciliation, hard guardrails, constrained
  policies, replay, explicit activation, idempotent writes, and an audit chain.
- A second domain, accounts-payable invoice variance, exercises shared governance.
- The public app routes are primarily HTML-oriented. There is no generic decision
  API, external outcome API, or customer-pipeline SDK yet.
- Agent usage is recorded, but gateway request IDs and actual serving versions are
  not currently correlated into decision receipts.
- The existing returns `handle_event()` can execute synthetic business actions.
  Calling it from a new API is NOT sufficient to implement a no-write harness.
- Tests use a test-only model fake. There is no shipped scripted provider or
  deployment-specific runtime to retain.

Relevant code: [providers](../app/agent/providers/__init__.py),
[agent construction](../app/agent/build.py),
[orchestration](../app/orchestrator.py),
[domain contracts](../app/domains/__init__.py),
[policy evaluation](../app/policy/engine.py), and
[policy activation](../app/policy/store.py).

### Mubit checks made for this plan

- Authenticated `GET https://api.mubit.ai/openai/v1/models` returned HTTP 200,
  35 model entries, and `x-middleware-request-id`.
- This read-only endpoint did not return workspace settings or serving state.
- Public `GET https://api.mubit.ai/openapi.json` returned HTTP 404.
- Current public documentation confirms provider-compatible paths, stable model
  IDs, task grouping, sessions, request IDs, kept examples, and adapter testing.
- The public adapter guide describes console comparison, not a complete public
  automatic-serving/control API. The supplied console settings expose additional
  rollout behavior that must be checked against this workspace.
- No inference, training, promotion, or settings-changing request was made.

**Evidence boundary:** the settings below are the user's console snapshot,
version `starter@1:4bef478e0edcbdfd`, not an API-verified export. Capturing calls and
enabling training do not by themselves prove that a trained version serves the
specific Responses endpoint used by Precedenz.

## 3. Let Mubit own model promotion

Default design: keep the same Mubit gateway, provider model ID, and task shape.
If native automatic serving is supported for that route, Mubit changes the
serving backend after its gates pass. Precedenz does not rewrite model IDs.

Do NOT implement a polling loop that chooses the newest adapter, constructs an
undocumented adapter model ID, or changes the configured model after "Training
complete." Changing model IDs can also change task identity and fragment examples.

| Responsibility | Owner |
| --- | --- |
| Capture, curate kept examples, train and test versions | Mubit |
| Shadow, canary, live routing, and provider spot checks | Mubit, after capability verification |
| Task input/output contract and business constraints | Precedenz + customer |
| Authorize business automation or change its scope | Explicitly approved policy, never model promotion |
| Execute an action and verify the actual source-system write | Customer's existing executor |
| Correlate decisions, model calls, outcomes, and policy versions | Precedenz |

There are two independent permissions: a model version may answer a call, and a
business action may execute. Passing the first never grants the second.

## 4. What the supplied settings mean

Keep the current settings initially. Do not silently lower quality thresholds or
change organization-wide settings to make a demo complete faster.

| Setting from the snapshot | Consequence |
| --- | --- |
| `adapters.train=true`, `promotion.mode=auto` | Training and promotion are enabled by middleware; verify route eligibility separately. |
| `adapters.min_rows=25`, `min_nonempty_rows=10` | First training needs kept, eligible examples, not just 25 recorded requests. |
| `rows.max_row_bytes=262144` | A training example must fit within 256 KiB; a captured trace can still be too long. |
| `adapters.max_rows=250` | One training job takes at most 250 examples, newest first. |
| Refresh: 25 new rows and `min_new_fraction=0.5` | Retraining may need more than 25 new examples as the live version's dataset grows. |
| `adapters.replay_fraction=0.1`, `warm_start=false` | Retraining reuses some prior examples, but does not imply online incremental weight updates. |
| `adapters.daily_job_budget=0` | UI says no daily training limit; this does not mean inference or training is free. |
| `adapters.rate.max_per_day=200` | The reviewer has a per-task daily call-reading limit; more traffic is not automatically more usable evidence. |
| `adapters.gate.parity_points=5`, `min_judged_rows=20` | A version needs enough judged cases and near-provider scores before rollout eligibility. |
| `promotion.bar=0.9`, `floor=0.75`, `alpha=0.05` | Rollout has a target pass rate and a separate statistical safety floor, not just one "90%" test. |
| `promotion.min_pairs=50`, `min_sessions=10` | Rollout evidence needs graded pairs across distinct genuine sessions. |
| Shadow: 10%, max 100/day | Only some eligible calls produce silent-test evidence; customers never receive shadow replies. |
| Canary: 10%, at least 100 answered calls | A passing test does not imply immediate full rollout. |
| Audit: 2%, min 10/day, max 500/day/version | Live versions continue to be checked; exact scheduling remains Mubit-owned. |
| `promotion.allow_rules_only=true` | A live version can be prompt-only. "Live version" is not proof of trained weights. |
| `evals.judge=auto`, `evals.efficiency=false` | Grading is on, but the pasted configuration does not establish a cost/latency promotion gate. |
| `feedback.outcome=off` | Do not assume customer corrections are already consumed as model-training labels. Confirm the applicable feedback mechanism. |
| `data.retention_days=90` | Raw calls have a retention window; the supplied description says examples and versions retain their own data. |
| No task overrides | These defaults apply broadly; prefer a documented task-scoped change if an organizer authorizes a demo override. |

Treat `distill.*`, `gate.*`, and `inject.*` as separate memory/lesson controls until
their exact scope is confirmed. In particular, do not read
`gate.promotion_dry_run=true` as proof that adapter serving is disabled, or use
`gate.validation.accept=0.6` as a business-action confidence threshold.
Likewise, nullable `adapters.train_lanes` and `adapters.targets` do not prove that
this task is eligible or ineligible; read its actual status.

### Volume and timing implications

- Indicatively, the refresh condition is
  `max(25, ceil(0.5 * live_version_example_count))` new kept examples. Confirm the
  engine's exact counting before presenting this as an observed trigger.
- At 10% shadow sampling, 50 graded pairs may require roughly 500 eligible calls
  if one sampled call supplies one usable pair. Filtering can increase that.
- At 10% canary traffic, 100 canary answers suggests roughly 1,000 eligible calls
  during that stage. This is not an additional fixed quota for every route:
  Mubit decides eligibility, evidence reuse, and stage transitions.
- These stages are not necessarily parallel. Neither estimate is a guarantee or
  a reason to generate hundreds of paid calls without a budget.
- Current traces span investigation, decision-card, and policy-proposal tasks.
  Counts across different task fingerprints must not be added to reach 25.

Therefore promise **automatic adoption after the serving gates pass**, not
"the 25th example immediately replaces the provider."

## 5. Minimum architecture

```text
Existing pipeline: fetch authoritative state
    |
    | one HTTP call / thin Python client
    v
Precedenz decision service
    authenticate -> registered task -> validate facts/provenance
    -> approved-policy path, or one stable model step through Mubit
    -> validate output -> hard guards -> active-policy authority
    -> Sapling where the task requires semantic evidence checking
    -> record immutable receipt
    |
    +-- auto: bounded authorized action intent
    |           -> existing executor checks current state, acts idempotently
    |           -> reports actual outcome
    |
    +-- ask: existing human queue handles the exception

Mubit: capture -> task -> train -> test -> shadow -> canary -> live
       Same gateway route; only verified serving metadata identifies the backend.
```

No outbound business-system writes from the new decision service. An `auto`
response is not a claim that an action has already happened.

The model's report stays advisory. Re-derive facts through the configured source
adapter where available, and reconcile the report against the trusted fact
snapshot before authorization. A smaller model confidently reporting a clean
case cannot override a serial mismatch or invent missing evidence.

Start with a single-customer deployment, one configured Mubit project/key, and
one returns task integrated into two caller workflows. The invoice domain is
an extension proof, not a requirement to train a second model tonight.
Do not build production multi-tenancy tonight.
Tenant labels in metadata are not a training-isolation boundary; separate
customers need separately authorized deployments/projects/keys unless Mubit
documents stronger isolation.

### Task contract

A trusted server-side contract defines the task ID, version, input and output
types, allowed actions, ceilings, hard checks, policy family, stable prompt,
source/provenance requirements, and any semantic verifier.

Use the existing domain-pack interface. Register contracts in code;
do not build YAML rule generation, arbitrary JSON predicates, or a workflow DSL.
Generalizing an interface is useful; removing domain-specific checks is not.

Caller-supplied facts are caller assertions, even when well-typed. Auto-action
requires the contract to trust that authenticated source or re-derive the facts
through a trusted resolver. Never treat a body field such as `verified=true` as
independent evidence. Missing or stale evidence returns `ask`.

The existing returns UI remains a working demo. A new integration's `ask` goes
to its caller's existing approval queue; a generic approval UI is not required.

## 6. Integration contract to implement

Three endpoints, plus a small Python client. These are proposed APIs.

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/decide` | Accept task, case reference, typed state, source revision, and idempotency key; return `auto` or `ask`. |
| `GET /v1/decisions/{id}` | Read the persisted receipt and completion state, including trace correlation and actual outcome if reported. |
| `POST /v1/outcomes` | Append execution results or authorized human corrections linked to a decision. |

Task IDs resolve to server-owned configuration, not a caller-selected model,
arbitrary tool, policy expression, workspace, or gateway URL.

Planned caller experience:

```python
decision = precedenz.decide(
    task="returns.missing_accessory",
    case_ref=case.id,
    state=source_system.read_verified_facts(case.id),
    source_revision=case.revision,
    idempotency_key=event.id,
)
if decision.status == "auto":
    existing_executor.execute_if_current(
        decision.actions,
        expected_revision=decision.source_revision,
        idempotency_key=decision.id,
    )
else:
    existing_approval_queue.add(case, reason=decision.reason)
```

The executor must recheck state/version, enforce its own action permissions and
caps, and report completion. A receipt alone does not prevent a customer from
ignoring those requirements.

Response fields: decision ID, status, bounded actions or escalation reason,
task/contract version, policy version, input hash, source revision, expiry,
requested model, actual serving identity when known, Mubit request IDs, and
trace/receipt location. Mark serving identity `unknown` when evidence is absent.

Use a demo API token separate from the human approval token. Derive authorized
scope from server credentials, never from a workspace ID in the request body.
Do not expose a public deployment with placeholder tokens.

Same idempotency key + same input returns the original result. Same key +
different input returns a conflict. Concurrent duplicate requests must not
create two final decisions. Malformed requests are 4xx errors; model failure,
unsafe output, policy conflict, or verifier failure never produces `auto`.

## 7. Automatic model adoption: first prove the mechanism

Time-box this investigation to 20 minutes before implementation:

1. Identify the task and version already collecting this app's examples. Record
   kept rows, version kind, test result, and current serving stage from the
   console or a documented read-only control API.
2. Confirm that automatic adapter serving supports this workspace's exact
   endpoint, streaming behavior, tool schemas, and structured output. Responses
   capture alone does not establish Responses adapter-serving support.
3. Ask Mubit which documented field/API identifies the actual answering version,
   how to inspect promotion progress, and how supported rollback works.
4. Confirm whether a passed version needs an additional route/serving
   configuration. `promotion.adapters={}` is not proof of route activation.
5. Confirm how to submit a human-corrected example, if supported, and whether
   `feedback.outcome=off` affects it. Do not guess a feedback endpoint.

Preserve the current returns task fingerprint for the MVP: model ID,
instructions, tools, tool choice, and output format. Wrap its existing read-only
investigation driver; do not replace it with a new generic prompt before showing
the existing adapter's progress. A new prompt or output schema can create a new
task with no accumulated examples. New domains get their own stable task and
cold-start count; they do not inherit the returns adapter.

### Routing behavior

- **Native automatic serving verified:** send the same requests to the same
  gateway. Mubit manages transitions; no local model-ID switching code is needed.
- **Supported serving alias/control API supplied by Mubit:** implement only that
  documented integration behind a small capability interface. Keep routing
  management outside the decision request's critical path.
- **Neither verified in the time box:** ship the harness and capture integration,
  show the real training/test stage, and label automatic serving as unverified or
  blocked. Do not present console comparison as production model switching.

In all cases, a trained or `Passed` candidate is not automatically `Live`. A
shadow reply must never be returned to the customer. A prompt-only version must
not be labeled a fine-tuned adapter.

"Better" means it cleared the applicable quality gates and still satisfies the
application's contract. Claim cheaper or faster only after measuring those
dimensions. Provider answer similarity is not business correctness.

Keep each in-flight decision's receipt immutable. Revalidate active policy
authority before issuing `auto`, and record the version actually observed, not
the latest version fetched later. An existing receipt is not silently upgraded.

Provide a local task pause that changes future results to `ask`. This contains
business risk but does not pretend to roll back Mubit's model. Model rollback
uses only Mubit's confirmed control mechanism. An outage never falls back to a
direct provider call outside Mubit.

## 8. Traces, outcomes, and data

### Correlation

- Preserve stable system instructions and schemas. Put case-specific values in
  input, not in a newly generated system prompt or schema.
- Use a genuine case/workflow session ID in supported request metadata. Keep one
  session across that case's model calls; do not invent sessions to meet a gate.
- Instrument the model provider's HTTP/SDK response boundary to capture
  `x-middleware-request-id` for every underlying call, including retries and
  streaming. One Strands invocation can contain several gateway requests.
- Use request-scoped context, not a process-global "current case." Collect
  transport metadata in memory and persist it after the model call; do not share
  a database connection across transport callbacks.
- Keep requested model, actual serving identity, and metadata provenance
  separate. The requested `gpt-6-luna` alias or `response.model` alone is not proof
  that an adapter served the call.
- Never invent a serving-version header. If the documented gateway headers do
  not expose it, use a confirmed control API or console correlation, otherwise
  record `unknown`.
- A deterministic policy fast path may make no model call. Its receipt says so;
  do not issue unnecessary paid calls merely to create training data.

### Outcome ledger

Persist immutable decisions and append-only outcomes, linked into the existing
audit log. Store task/contract/policy version, canonical input hash, source
revision, proposed and authorized actions, serving evidence, all request IDs,
timings, available token usage, execution result, and authorized corrections.

Use short transactions: reserve an idempotent decision, release the write
transaction for network calls, then atomically finalize its receipt and audit
entry after checking current authority. Handle duplicate/in-progress requests
explicitly. Do not hold the audit writer lock during model inference.

Execution reported by a caller is not the same as an independently verified
outcome. Derive `reported`, `human_confirmed`, or `verified` status from the
authenticated actor and verification path, not from an untrusted boolean.
Reject unknown decision IDs, cross-task updates, and invalid corrected actions.
Keep corrections as new events, not edits to history.

A verified human overrule should pause the affected task's automatic actions
until its policy is reviewed and replayed. An unauthenticated or unverified
failure report must not be able to pause unrelated tasks.

The first feedback deliverable is this local ledger. Export/deliver verified
labels only through a confirmed Mubit mechanism; otherwise expose
`delivery=not_configured`. Keep failed or pending deliveries visibly distinct
from accepted training examples. Never train from unchecked automatic actions.

Mubit's existing captured examples are a separate dataset: do not claim local
verification automatically changes Mubit's kept-example selection. Confirm
correction, keep/drop, and ingestion semantics before making that claim.

### Privacy and metrics

Only approved customer data goes to Mubit. Explain raw-record versus
training-artifact retention and redact secrets before capture. Metadata-only
capture is an explicit privacy option, not a training path.

Report schema-valid rate, approved automation, escalations, verified overrides,
actual execution success, latency, and token usage separately. Add actual
adapter adoption only where serving evidence exists. Unknown cost stays unknown;
requested-model pricing is not an adapter bill.

Freeze a small, versioned held-out boundary set separately from training traffic.
The existing replay corpus proves policy behavior on that corpus; it is not
automatically a held-out model benchmark. For hosted held-out checks, the
documented `x-middleware-record: metadata` option prevents those requests/replies
from becoming kept training examples.

Compare provider and adapter on identical held-out inputs only through a
supported comparison/pinning mechanism. If that mechanism is console-only, label
the evidence as a console comparison rather than claiming a controlled API
benchmark. A model-version change alone is not evidence of improvement.

## 9. Smallest code changes

Proposed new paths are not existing APIs or shipped modules.

| Area | Change |
| --- | --- |
| `app/harness/contracts.py` | Trusted task registry, typed envelopes, provenance rules, read-only task-driver interface. |
| `app/harness/service.py` | Pure evaluate-and-authorize path; reuse schema, guardrails, matching, and Sapling; return action intents only. |
| `app/harness/store.py` | Idempotent decision reservation/finalization, receipt reads, outcome appends, scoped pause. |
| `app/api/decisions.py` | The three authenticated machine endpoints; no approval or policy activation by model output. |
| Existing `app/main.py` | Mount the router without replacing current pages or exposing admin credentials. |
| Existing provider adapter | Request-scoped metadata and transport correlation; retain the working Mubit endpoint and task fingerprint. |
| Existing domain/policy modules | Narrow extraction of read-only evaluation seams; use the `Facts` protocol where needed, not a new policy language. |
| Paired database migration | Decision/outcome/pause persistence for both SQLite and PostgreSQL. |
| `examples/` | Thin Python client, a standalone pipeline, and a Strands tool using the same HTTP contract and existing dependencies. |
| `tests/` | New API, trace, contract, duplicate, and no-business-write regression tests using existing test-only fakes. |

The new service must not call the action-executing returns `handle_event()`.
Reuse its investigation and evaluation pieces, or extract the small read-only
boundary. Keep existing UI execution behavior unchanged.

The MVP client is a small importable example, not a rushed published SDK.
HTTP is the compatibility layer for other languages and orchestrators. Add the
invoice task only after the same API works from both caller examples.

## 10. Time-boxed build order

The supplied hard stop is Sunday 2026-10-25 at 07:00 Europe/London, not 10:00.
At approximately 21:28 BST on Saturday there were about 10.5 elapsed hours left,
including the clock change. Schedule in UTC or elapsed time and display the local
timezone; 01:xx local time repeats. Do not rely on the contradictory submission-opening
wording to extend the code deadline.

| Priority / budget | Deliverable | Acceptance |
| --- | --- | --- |
| P0 / 20 min | Verify Mubit serving and feedback capabilities. | A written yes/no/unknown for the actual route, serving identity, and supported controls. |
| P0 / 90 min | One registered returns task, pure decision service, API auth and persistence. | Unknown case asks; approved matching case returns a bounded intent; zero business writes. |
| P0 / 45 min | Gateway correlation and stable metadata. | A receipt joins to real gateway requests without exposing keys or changing task shape. |
| P0 / 45 min | Thin client + two caller workflows. | Both call the same contract without importing app internals or changing the customer executor. |
| P0 / 30 min | Outcome ledger and scoped safety pause. | Authorized correction is recorded; future unsafe automation is paused. |
| P1 / 30 min | Minimal receipt/status presentation using current UI patterns. | Shows real policy, request IDs, serving evidence, and unknown/collecting stages honestly. |
| P0 / 60 min | Regression and integration checks. | Existing suite passes; API has duplicate, no-write, failure, and backend tests. |
| P0 / 90 min | Record and verify the product demonstration. | A playable video of actual behavior and real Mubit evidence. |

Approximately 6 h 50 min total, leaving a buffer for training delays, failures,
and submission. Start training observation now; it runs in parallel with build
work. Do not wait for training to finish before implementing the API.

At each time-box boundary, cut scope rather than extending it:

1. Preserve API + no-write safety + one task + trace receipts + two callers.
2. Cut the invoice integration, custom status screen, and elaborate client
   packaging before cutting tests or authority checks.
3. If live routing has not cleared Mubit's gates, show its true current stage.
   An organizer-authorized, documented task-only demo setting is an option;
   silently weakening global settings is not.

Not tonight: a new trained model family, Jev/Clef compatibility, arbitrary
customer connectors, Slack/Teams delivery, a generic approval/workflow builder,
transparent OpenAI proxy/SSE parity, production multi-tenancy, or a second
training/rollout control plane. Do not restore removed runtime components.

## 11. Required tests and definition of done

- Existing returns UI and governance regressions still pass.
- API decision requests never call warehouse, ledger, or other business-write
  functions; tests replace those functions with failures to prove the boundary.
- Two caller workflows work through HTTP; no caller imports the application's
  orchestrator or database.
- Unknown task, malformed state, stale evidence, missing provenance, invalid
  output, missing policy, conflicting policy, and verifier outage cannot auto-act.
- Action types and caps come from the registered contract and active policy,
  never arbitrary model-generated executable content.
- A hallucinated fact or confident false model report cannot override the
  trusted source snapshot or bypass reconciliation.
- Same-key retries are stable; changed-input reuse conflicts; concurrent
  duplicates cannot finalize two decisions.
- Parallel cases do not share session IDs, request IDs, facts, or credentials.
- Streaming and retried calls retain their own actual gateway request IDs.
- Unapproved, merely trained, shadow, and prompt-only versions are not mislabeled
  as live trained adapters.
- A serving-version change does not expand action permissions or alter an
  existing receipt. Unsupported serving metadata remains unknown.
- Outcomes require authorized scope and valid typed corrections; local-only
  feedback is never displayed as delivered to Mubit.
- SQLite and PostgreSQL have matching migrations and tests. Run PostgreSQL
  checks when a throwaway local test database is available; report skips.
- No hosted calls in the normal regression suite. Real traffic has a declared
  budget and uses only synthetic or approved data.

Done means the integration boundary and safety work, not merely a dashboard that
shows "adapter enabled." Automatic adoption is a demonstrated capability only
after a real request can be tied to the serving version.

## 12. Demonstration

1. A customer-like pipeline calls Precedenz for the existing returns decision.
2. A receipt shows the policy checks and links to the gateway's actual calls.
3. The same integration is used as a node in a different caller workflow.
4. Show Mubit's real version stage. If promoted, show a later call using the
   actual version without changing the customer's code or model ID.
5. The risky serial-mismatch case still asks a person after that change.
6. The customer's executor, not Precedenz, records the successful write and
   reports its outcome. A verified correction demonstrates the scoped pause.

Closing line: **Your pipeline stays. The model can improve. Its authority does
not grow.**

Show measured accuracy, latency, or cost only with its sample count, provider,
serving evidence, and method. Do not substitute test-fake timings, fabricated
session traffic, or a console-comparison result for a live rollout.

## Sources and open evidence

- [Gateway API and headers](https://docs.mubit.ai/reference/api)
- [Task grouping and sessions](https://docs.mubit.ai/guides/tasks-and-sessions)
- [Keep provider model IDs](https://docs.mubit.ai/guides/model-ids)
- [Adapter training and comparison](https://docs.mubit.ai/guides/first-adapter)
- [Project-scoped Mubit key permissions](https://docs.mubit.ai/admin/mubit-keys)
- User-supplied console settings, version `starter@1:4bef478e0edcbdfd`.

Open evidence: documented workspace-settings/status API, route-specific native
adapter serving, serving-version correlation, supported rollback, and verified
correction ingestion. A provider model-list response does not resolve these.
