# Mubit research and workspace reference

Compiled: 2026-10-24. Scope: findings from this conversation, its saved earlier
thread, current repository inspection, public documentation, and the user's
workspace-settings snapshot. No credentials are included.

Related: [implementation plan](meta-harness-plan.md),
[hackathon reference](hackathon-notes.md), and
[implementation/data disclosures](provenance.md).

## 1. Evidence and terminology

- **Verified this session:** repository behavior inspected locally, current public
  gateway documentation fetched, and the read-only API probe described below.
- **Console snapshot:** settings supplied by the user, version
  `starter@1:4bef478e0edcbdfd`. These were not retrieved through a settings API.
- **Earlier research:** information recovered from the earlier conversation.
  Measurements quoted there were not independently rerun in this documentation task.
- **Proposal:** product or architecture ideas, not existing Mubit APIs or shipped
  Precedenz functionality.

Keep capture enabled, training enabled, trained, test-passed, shadow, canary,
and live separate. These are not interchangeable states.

## 2. What Mubit does

Mubit sits between an application and its model provider. The documented gateway
integration changes the client's base URL and key while retaining provider model
IDs. It records calls, groups recurring calls into tasks, and trains small
task-specific adapters when enough eligible examples have been kept.

The broader runtime-calibration story discussed in this session is: connect the
runtime, capture experience and outcomes, calibrate a later run, evaluate before
rollout, and retain a rollback path. The useful evaluation measures are task
success, repeated errors, latency, and cost per successful task on held-out work.

Three distinct pieces must not be conflated:

| Piece | Role |
| --- | --- |
| Gateway/task adapters | Capture recurring model calls, collect examples, train/test task-specific versions, and use supported serving mechanisms. |
| Runtime memory/lessons | Distillation, retrieval/injection, attribution, and scoped promotion; reflected in several advanced setting namespaces. |
| Banyan Sapling | Policy/evidence/claim verification with conservative uncertainty handling. |

The landing-page research described Banyan as Mubit's open-foundation model
family, trained using task experience and evaluated outcomes. That is a product
direction, not permission to treat every gateway task as a new Banyan model.

## 3. Gateway integration

Documented base host: `https://api.mubit.ai`. Use the URL shown by the workspace's
Connect page if it differs.

| Provider/API family | Client base / prefix |
| --- | --- |
| OpenAI | `https://api.mubit.ai/openai/v1` |
| Anthropic | `https://api.mubit.ai/anthropic` |
| Gemini | `https://api.mubit.ai/gemini` |
| Supported OpenAI-compatible services | `/compat/<provider>/v1` |

OpenAI-compatible services discussed in the research include OpenRouter, Groq,
Together, and Mistral. Gateway support for an API does not establish adapter
training or automatic serving support for every model and request shape.

The OpenAI prefix documents Chat Completions, Responses, embeddings, and
`GET /models`. Anthropic documents messages, token counting, and model listing;
Gemini documents its native generation/streaming paths.

### Authentication and headers

- A Mubit key starts with `mbt_`; load it from `MUBIT_API_KEY`.
- OpenAI-compatible clients send `Authorization: Bearer <Mubit key>`.
- Anthropic uses `x-api-key` or bearer authentication; Gemini uses
  `x-goog-api-key`.
- Send only the Mubit key. Provider keys belong in the Mubit console, not beside
  the Mubit key or in custom per-request fields.
- Keys are project-scoped. Console documentation distinguishes inference and
  read-only keys and admin versus member permissions.
- Calls are server-side. The documented gateway does not answer browser CORS
  preflights; do not expose the key in frontend code.
- Mubit's request ID is returned in `x-middleware-request-id`.
- `x-middleware-record` accepts `full`, `metadata`, or `off`.
- `x-middleware-store: false` also excludes request/reply content from storage.
  Metadata-only calls cannot become kept examples without that content.
- `off` also drops the task fingerprint. A request can lower recording, not
  override a workspace restriction to increase it.
- `x-middleware-*` headers are not forwarded to the provider. Cookies and
  forwarding headers are also stripped.
- Mubit's gateway errors normally use the provider's error format with
  `middleware_gateway_error`, a code, and request correlation.

The API reference distinguishes forwarding from recording: larger calls may
still be forwarded whole while their stored record is truncated. It describes
up to 32 MiB of request recording and the first 4 MiB plus last 256 KiB of an
answer. Training eligibility has a much smaller per-row limit in this workspace.
Starter-credit restrictions and provider-key requirements are separate concerns.

### Actual read-only probe

Using the locally configured key without printing it, this session made one
authenticated `GET https://api.mubit.ai/openai/v1/models`, with metadata-only
recording and redirects disabled:

| Observation | Result |
| --- | --- |
| HTTP status | 200 |
| Model entries | 35 |
| Mubit request-correlation header | Present |
| Mubit-prefixed response header names on this GET | `x-middleware-request-id` |
| Workspace settings in the response | No |

This was not inference, a training request, or a list of 35 trained adapters.
Public `GET https://api.mubit.ai/openapi.json` returned HTTP 404. That does not
prove no control API exists; it means this discovery path did not expose one.
No settings, promotion, provider keys, or training configuration were changed.

## 4. Tasks, sessions, and examples

The documented task fingerprint includes model, instructions, tools, tool
choice, and output format. A task is not simply "all calls from this app."

Keep task instructions and schemas stable. Put case-specific state in input.
Changing the model ID, tool schema, or structured-output format may create a new
task and strand previously collected examples.

Sessions group related calls. Research identified supported metadata names such
as `session_id`, `thread_id`, and `conversation_id`. Use a genuine workflow/case
identifier and confirm the exact request shape for the selected provider.
Sessions do not replace task identity or provide tenant isolation.

A captured call is not necessarily a kept training example. Content recording,
size, nonempty content, curation, answerability, and route eligibility matter.
The console's kept-example count is the source of truth.

The public first-adapter guide uses 40 varied calls of one task as an example;
it warns that passing is not guaranteed and the calls consume provider/starter
credit. It describes collecting, reviewing, training, testing, and then console
comparison. Outcomes include `Passed`, `Below target`, `Malformed tool calls`,
`Prompt only`, and `Not tested`; availability also depends on serving support.

## 5. Training and automatic adoption

The user's workspace says training and automatic promotion are enabled. Its
configured rollout has silent testing, partial rollout, and continuing audits.
However, automatic serving on Precedenz's exact Responses/tool/streaming route
was not independently verified through a control API.

The correct integration design is to let Mubit own supported promotion and keep
the application's gateway route and provider model ID stable. Do not invent an
adapter model ID or build a competing "choose the newest version" loop.

Earlier statements that adapters categorically cannot be called were too
absolute. The precise finding is: public documentation viewed here describes
console comparison, and this session did not verify a direct adapter inference
ID or a complete serving-management API. The supplied automatic-rollout settings
require workspace-specific confirmation, not dismissal.

Important conditions from the supplied snapshot:

- First training needs 25 kept rows and at least 10 nonempty rows.
- Rows are limited to 262,144 bytes, or 256 KiB.
- Retraining needs 25 new rows plus a minimum-new fraction of 0.5. Indicatively,
  the required count can be `max(25, ceil(0.5 * live_version_example_count))`;
  exact counting remains to be confirmed.
- One training takes at most 250 examples, newest first; replay fraction is 0.1.
  Warm start is off, so "improves over time" does not mean online weight updates.
- Adapter gates allow a five-point difference from the provider and require
  20 judged test cases.
- Promotion has a 90% target, a separate 75% cautious-estimate floor, 50 graded
  pairs, and 10 sessions. These are different gates, not one quality percentage.
- Shadow sampling is 10%, capped at 100/day; canary is 10% for at least 100
  canary answers. Illustratively, those stages could need about 500 and 1,000
  eligible calls respectively. Actual counting, filtering, and evidence reuse
  are Mubit-owned.
- Audit sampling is 2%, with minimum/maximum daily settings and statistical checks.
- Prompt-only versions may go live. Serving a version is not proof of trained weights.
- Efficiency evaluation is off; passing quality gates does not prove lower
  cost or latency. Measure those separately.

Do not fabricate sessions, spam paid calls, or weaken shared quality thresholds
to satisfy a demo. "25 examples" does not mean "immediately live."

## 6. Banyan Sapling

Source: [MubitLabs/banyan-sapling](https://huggingface.co/MubitLabs/banyan-sapling).
The launch calls it the first Banyan model, an open-weight research model for
verifying claims against policies and evidence, including insufficient evidence.
Earlier research described an Apache-2.0 release; check the model card/license
for any deployment or redistribution.

The semantic inputs are `policy`, `evidence`, and `claim`. Precedenz's endpoint
client wraps them as:

```json
{"inputs": {"policy": "...", "evidence": "...", "claim": "..."}}
```

The four labels discussed are `supported`, `violation`,
`insufficient_information`, and `inconsistent_context`. Probabilities and a
calibrated status are returned; `unsure` is an abstention/status, not a business
authorization. Retrieval, source authentication, explanations, and the final
business action belong to the surrounding system.

Earlier research encountered a public report of 779 confident/correct answers
out of 1,731 cases, with the remainder unsure: approximately 45% coverage.
This was not reproduced here and is not a guarantee about all cases, domains,
or future actions. High confident-subset accuracy and high coverage are different.

### Current Precedenz usage

The returns workflow calls Sapling after an active policy matches and before a
business write. It receives policy text, raw source evidence including inspector
notes, and the claim that the case may be resolved automatically. It may block
an action; it cannot override a deterministic refusal.

Current code accepts raw `supported` with support probability at least 0.5.
It records calibrated status but does not require that status to be confident.
Thus it may accept raw support while calibrated status is `unsure`. This is a
demo implementation choice, not equivalent to honoring Sapling's abstention gate
or a calibrated guarantee; review it before production.

The existing README records earlier endpoint observations: CASE-2002 supported
at 0.70, CASE-2009 supported at 0.66, CASE-2007 insufficient information for a
scorched-battery-door note, and CASE-2008 inconsistent context for a contradictory
serial note. These were not rerun for this reference. Its recorded original
30-case raw-label check is not a current universal model benchmark.

Client: [app/sapling.py](../app/sapling.py). Default public demo endpoint:
`https://6adbee0669a00298cd4774c9.endpoints.huggingface.cloud`.
Sapling is now enforced by default; tests disable or stub it.

## 7. Latte Bench

Source: [MubitLabs/latte-bench](https://github.com/MubitLabs/latte-bench).
The launch asks whether models learn from measured feedback, adjust a simulated
latte-art plan, and improve across attempts. Earlier research compared runs with
and without feedback and distinguished quality from reliability.

The relevant lesson is that more context is not automatically useful learning:
feedback can improve or worsen behavior, so compare outcomes and regressions.
Simulated plan improvement does not by itself prove model-weight updates,
held-out transfer, or real-world pouring ability. Precedenz has not been
evaluated on Latte Bench in this session.

## 8. Feedback, customer models, and typed decisions

Human approval, human correction, deterministic verification, provider-answer
similarity, and model-weight updates are different signals.

Precedenz currently teaches replay-tested policy versions. No verified Mubit
outcome/correction ingestion API was integrated. The console snapshot says
`feedback.outcome=off`; confirm its exact scope and the supported correction
mechanism before claiming a human overrule trains the adapter.

Proposed customer training requires verified labels, held-out evaluation,
calibration, shadow mode, rollback, and explicit data isolation. A per-customer
adapter alone does not guarantee privacy, deletion, or freedom from bias.
Never use unchecked automatic actions as ground truth.

The proposed name "Banyan Taproot" was a discussion, not an existing model or
Mubit collaboration. Banyan is Mubit's family name; use independent branding
or obtain agreement rather than imply affiliation.

Jev/Clef-style typed decision APIs were discussed as a design reference:
state plus bounded questions and probabilities. Mubit is not established here
as a compatible decision-model API. An LLM can emit a constrained choice, but
self-reported confidence, token scores, or sampled agreement are not calibrated
business probabilities. The suggested compatibility layer and custom fine-tune
were not implemented; earlier competitor size/license claims were not validated.

## 9. Current Precedenz integration and remaining evidence

The configured default model is `gpt-6-luna` through the Mubit Responses route.
The repository uses Responses because its tested tool/reasoning combination did
not work on the Chat Completions path; do not generalize that to every model.
Optional direct-provider adapters remain, but the planned harness uses Mubit.

Useful environment variables: `MUBIT_API_KEY`, `PRECEDENZ_MODEL_PROVIDER`,
`PRECEDENZ_MUBIT_MODEL_ID`, `PRECEDENZ_MUBIT_BASE_URL`,
`PRECEDENZ_SAPLING_MODE`, `PRECEDENZ_SAPLING_URL`,
`PRECEDENZ_SAPLING_MIN_SUPPORT`, and `PRECEDENZ_SAPLING_TIMEOUT_SECONDS`.
Current Sapling support/timeout defaults are 0.5 and 30 seconds.

The last code verification had 213 passing tests, four skips, and three
deselected live tests. The 24-case regression report uses a test-only model fake.
Neither is a measurement of Mubit model accuracy, serving latency, or cost.

The new meta-harness is planned, not shipped: HTTP decisions, immutable receipts,
caller-owned execution, request-ID correlation, verified outcomes, and a scoped
pause. Preserve the existing task shape before creating new generic prompts.

Still unverified: settings/status control API, exact native serving eligibility,
actual serving-version correlation, supported rollback/pinning, and correction
ingestion. Requested model ID or `response.model` alone is not adapter evidence.
The model-list probe does not answer any of these questions.

## 10. Supplied console summary

Source: user-provided workspace snapshot, not an authenticated settings export.
Service status displayed: `Operational`. Settings version:
`starter@1:4bef478e0edcbdfd`. No task overrides were configured.

| UI field | Displayed value |
| --- | --- |
| Going live / approval | Automatically, once it passes; set by Middleware |
| Silent test | 10% of calls, up to 100/day |
| Partial rollout | 10% of calls, for at least 100 calls |
| Quality bar | 90% of test cases; set by Middleware |
| Train new versions | After 25 new examples |
| Longest example | Up to 256 KB each |
| Training limit | No daily limit; set by Middleware |
| Checking live versions | Provider reanswers 2% of adapter-answered calls |
| Grading | On |
| Adapter pass bar | Within five points of the provider model |
| Live minimum | 20 judged test cases |
| Calls kept | 90 days |
| Task-specific settings | None; every task uses the settings above |

## 11. Complete advanced settings snapshot

All 181 engine keys supplied in the conversation are retained here. Values and
their "Set by" labels are copied as displayed, including floating-point artifacts.
`(empty)` means a blank UI cell. The displayed value `none` is preserved as
`none`; it is not interpreted as a particular JSON or SDK representation.
UI-summary and engine provenance labels are retained without trying to resolve
differences. These tables are a historical snapshot, not a recommended settings
patch or proof that every subsystem is active.

### Capture and distillation

| Key | Value | Set by |
| --- | --- | --- |
| `capture.content` | `messages` | Default |
| `capture.min_chars` | `20` | Default |
| `capture.human_importance_floor` | `(empty)` | Default |
| `distill.owner` | `builtin` | Default |
| `distill.trigger.on_run_end` | `false` | This project |
| `distill.trigger.every_n_events` | `10` | This project |
| `distill.trigger.negative_streak` | `0` | Default |
| `distill.trigger.variance` | `0` | Default |
| `distill.trigger.variance_window` | `10` | Default |
| `distill.evidence.max_items` | `200` | Default |
| `distill.evidence.per_type_newest` | `20` | Default |
| `distill.evidence.include_step_outcomes` | `true` | Default |
| `distill.evidence.include_linked_runs` | `false` | Default |
| `distill.max_units` | `5` | Default |
| `distill.default_breadth` | `run` | Default |
| `distill.on_timeout` | `skip` | Default |
| `distill.prompt_append` | `(empty)` | Default |
| `distill.schema` | `none` | Default |
| `distill.lesson_types` | `success, failure, observation, rule, preference` | Default |
| `distill.initial_confidence` | `0.5` | Default |
| `distill.job_ttl_s` | `600` | Default |
| `distill.job_max_attempts` | `3` | Default |

### Gates

| Key | Value | Set by |
| --- | --- | --- |
| `gate.mode` | `auto` | Default |
| `gate.auto_up_to` | `global` | This project |
| `gate.ladder` | `run, session, global, org` | Default |
| `gate.validation.enabled` | `true` | Default |
| `gate.validation.accept` | `0.6000000238418579` | Default |
| `gate.validation.reject` | `0.25` | Default |
| `gate.recurrence.threshold` | `3` | Default |
| `gate.recurrence.activation_recurrence` | `0` | Default |
| `gate.recurrence.identity` | `normalized_prefix_100` | Default |
| `gate.recurrence.similarity_floor` | `0.5` | Default |
| `gate.min_actors` | `2` | Default |
| `gate.promotion_dry_run` | `true` | Default |
| `gate.audition.enabled` | `true` | Default |
| `gate.audition.min_per_arm` | `5` | Default |
| `gate.audition.epsilon` | `0` | Default |
| `gate.audition.deadline_s` | `604800` | Default |
| `gate.rules_promote` | `false` | Default |
| `gate.notes_gated` | `true` | Default |
| `gate.overlay_gate` | `true` | Default |
| `gate.overlay_gate_legacy_visible` | `true` | Default |
| `gate.client_confidence_max` | `0.800000011920929` | Default |
| `gate.job_ttl_s` | `86400` | Default |
| `gate.on_timeout` | `reject` | Default |
| `gate.runs_per_arm` | `1` | Default |
| `gate.runs_per_arm_sampled` | `3` | Default |

### Injection

| Key | Value | Set by |
| --- | --- | --- |
| `inject.phases` | `step` | Default |
| `inject.budget_tokens` | `2048` | Default |
| `inject.timeout_ms` | `1500` | Default |
| `inject.overlay_top_k` | `10` | Default |
| `inject.overlay_token_budget` | `1600` | Default |
| `inject.noise_floor` | `0.550000011920929` | Default |
| `inject.overlay_min_sim` | `0` | Default |
| `inject.lesson_embed` | `true` | Default |
| `inject.kinds` | `lesson, rule, procedure, fact` | Default |
| `inject.min_confidence` | `0` | Default |
| `inject.fail` | `open` | Default |
| `inject.cache_ttl_s` | `30` | Default |
| `inject.conditions` | `cel` | Default |
| `inject.render_fields` | `false` | This project |
| `inject.condition_budget_ms` | `50` | Default |

### Attribution and sampling

| Key | Value | Set by |
| --- | --- | --- |
| `attribute.signal_map` | `failure: -1, neutral: 0, partial: 0.25, success: 1` | Default |
| `attribute.learning_rate` | `0.10000000149011612` | Default |
| `attribute.fanout_learning_rate` | `0.05000000074505806` | Default |
| `attribute.run_outcome_fanout` | `true` | Default |
| `attribute.actor_reinforce_cap` | `0` | Default |
| `attribute.credit` | `receipt` | Default |
| `attribute.derived` | `usage` | Default |
| `sample.max_per_day` | `5` | Default |

### Model promotion and live checks

| Key | Value | Set by |
| --- | --- | --- |
| `promotion.mode` | `auto` | Middleware, locked |
| `promotion.adapters` | `{}` | Default |
| `promotion.bar` | `0.9` | Default |
| `promotion.floor` | `0.75` | Default |
| `promotion.alpha` | `0.05` | Default |
| `promotion.min_pairs` | `50` | Default |
| `promotion.min_sessions` | `10` | Default |
| `promotion.shadow_rate` | `0.1` | Default |
| `promotion.shadow_max_per_day` | `100` | Default |
| `promotion.canary_percent` | `10` | Default |
| `promotion.min_canary_calls` | `100` | Default |
| `promotion.audit_rate` | `0.02` | Default |
| `promotion.audit_max_per_day` | `500` | Default |
| `promotion.audit_min_per_day` | `10` | Default |
| `promotion.audit_alpha` | `0.01` | Default |
| `promotion.audit_block` | `200` | Default |
| `promotion.audit_window` | `1000` | Default |
| `promotion.margin_points` | `5` | Default |
| `promotion.allow_rules_only` | `true` | Default |
| `promotion.stable_steps` | `false` | Default |
| `promotion.stable_reasks_max_per_day` | `300` | Default |
| `promotion.advisory_entry` | `shadow` | Default |
| `promotion.advisory_canary_percent` | `50` | Default |

### Adapter training, rows, and lanes

| Key | Value | Set by |
| --- | --- | --- |
| `adapters.train` | `true` | Middleware, locked |
| `adapters.train_lanes` | `none` | Default |
| `adapters.min_rows` | `25` | Default |
| `adapters.min_nonempty_rows` | `10` | Default |
| `adapters.max_rows` | `250` | Middleware |
| `adapters.replay_fraction` | `0.1` | Default |
| `adapters.answerability` | `figures` | Middleware |
| `adapters.warm_start` | `false` | Default |
| `adapters.submit.strict_recipe` | `true` | Default |
| `adapters.targets` | `none` | Default |
| `adapters.learning_rate` | `none` | Default |
| `adapters.distill_rules` | `true` | Default |
| `adapters.rank` | `none` | Default |
| `adapters.scale` | `2` | Default |
| `adapters.layers` | `none` | Default |
| `adapters.mask_prompt` | `true` | Default |
| `adapters.mix.base_behaviour` | `0.15` | Default |
| `adapters.mix.control` | `0.05` | Default |
| `adapters.row_format` | `fold` | Default |
| `adapters.refresh.min_new_rows` | `25` | Default |
| `adapters.refresh.min_new_fraction` | `0.5` | Default |
| `adapters.cooldown_minutes` | `0` | Default |
| `adapters.daily_job_budget` | `0` | Default |
| `adapters.rate.max_per_day` | `200` | Middleware |
| `rows.max_row_bytes` | `262144` | Default |
| `adapters.plateau.window` | `3` | Default |
| `adapters.plateau.min_row_growth` | `0.5` | Default |
| `adapters.gate.parity_points` | `5` | Default |
| `adapters.gate.min_judged_rows` | `20` | Default |
| `adapters.gate.constant_output` | `refuse` | Default |
| `lanes` | `{}` | Default |

### Evaluation and data retention

| Key | Value | Set by |
| --- | --- | --- |
| `evals.kinds` | `contains, regex, number, all, any, figures_in_input, tool_call, json_schema` | Default |
| `evals.judge` | `auto` | Default |
| `evals.stable_steps` | `false` | Default |
| `evals.differs_from_base` | `false` | Default |
| `evals.efficiency` | `false` | Default |
| `data.retention_days` | `90` | Default |
| `data.retention_max_days` | `3650` | Default |
| `data.train_opt_out` | `none` | Default |

### Islands

| Key | Value | Set by |
| --- | --- | --- |
| `islands.census` | `off` | Default |
| `islands.census_every` | `100` | Default |
| `islands.charter` | `off` | Default |
| `islands.plan` | `off` | Default |
| `islands.areas_auto` | `off` | Default |
| `islands.key` | `lane_position_tool` | Default |
| `islands.text_merge_jaccard` | `0.8` | Default |
| `islands.min_rows` | `100` | Default |
| `islands.min_checks_certify` | `100` | Default |
| `islands.min_checks_train` | `100` | Default |
| `islands.checks_per_session` | `2` | Default |
| `islands.max_regions` | `5` | Default |
| `islands.call_check` | `tool_args` | Default |

### Census and ladder

| Key | Value | Set by |
| --- | --- | --- |
| `census.bases` | `Qwen/Qwen3-4B-Instruct-2507, Qwen/Qwen3-8B` | Default |
| `census.reference_bases` | `Qwen/Qwen3-1.7B` | Default |
| `census.k` | `8` | Default |
| `census.min_pass_at_k` | `0.3` | Default |
| `census.continue_pass_at_1` | `0.5` | Default |
| `census.continue_pass_at_k` | `0.9` | Default |
| `census.best_of_n_gap` | `0.2` | Default |
| `ladder.rung_bar` | `0.95` | Default |
| `ladder.min_checks` | `35` | Default |
| `ladder.neighbours` | `4` | Default |
| `ladder.restate_min` | `0.9` | Default |

### Curves and certification

| Key | Value | Set by |
| --- | --- | --- |
| `curves.row_counts` | `25, 50, 100, 200` | Default |
| `curves.learning_rates` | `0.0001, 0.0002, 0.0005` | Default |
| `curves.epochs` | `3` | Default |
| `curves.warmup_share` | `0` | Default |
| `curves.overfit_rows` | `20` | Default |
| `curves.data_limited_from_rows` | `50` | Default |
| `curves.data_limited_points` | `10` | Default |
| `curves.stop_rounds` | `2` | Default |
| `curves.stop_gain_points` | `5` | Default |
| `curves.candidate_lift_points` | `10` | Default |
| `certify.mode` | `off` | Default |
| `certify.split_b_share` | `0.5` | Default |
| `certify.max_failures` | `4` | Default |
| `certify.alpha` | `0.05` | Default |
| `certify.expiry_days` | `30` | Default |

### Feedback and judge

| Key | Value | Set by |
| --- | --- | --- |
| `feedback.outcome` | `off` | Default |
| `judge.view` | `v1` | Middleware, locked |
| `judge.format` | `o1` | Middleware, locked |
| `judge.rubric` | `r_c` | Middleware, locked |
| `judge.sp_l` | `none` | Default |
| `judge.id` | `j1-2026-10-10-lite-rc-rule+flash-rd` | Middleware, locked |
| `judge.model` | `gemini-3.1-flash-lite` | Middleware, locked |
| `judge.pair` | `v1/o1/r_d@gemini-3.8-flash` | Middleware, locked |
| `judge.checklist` | `rule` | Middleware, locked |
| `judge.curation` | `off` | Middleware, locked |

### Additional meaning supplied in the UI help

- Promotion pairs count graded spot checks at or above the version's match
  cut-off; `min_sessions` counts sessions contributing those checks.
- `promotion.audit_alpha` is the error budget shared by the two safety checks
  over a version's life. `audit_block` is the spot-check interval between starts
  of safety-check runs; `audit_window` is the number covered by a check.
- `promotion.margin_points` is the allowed provider-preference gap before the
  comparison check counts it. It is not an app spend or confidence threshold.
- Prompt-only go-live permission is a person-controlled setting.
- The adapter parity help says a five-point bar, with ten more points when the
  test cases cannot distinguish the models. The parity bar and minimum judged
  rows are admin-controlled, and apply before canary or live eligibility.
- Changing evaluation kinds starts a new test set, not a directly comparable
  continuation of the old one. With judging off, the comparison check is off.
- Raw-call retention does not erase separately retained examples and versions.
  `data.train_opt_out` excludes routes from training without deleting data.
- The reviewer limit, daily training limit, example count, and example size are
  separate controls. None means "unlimited free inference."
- Nullable lane/target/base-selection fields and inactive islands/certification
  controls must not be promoted into claims about the actual adapter's recipe,
  model size, or certification.

## 12. Sources, documentation caveats, and research leads

Primary sources consulted:

- [Mubit landing page](https://mubit.ai/)
- [Current documentation](https://docs.mubit.ai/)
- [Documentation index](https://docs.mubit.ai/llms.txt)
- [Full documentation](https://docs.mubit.ai/llms-full.txt)
- [Gateway API reference](https://docs.mubit.ai/reference/api)
- [Task and session guide](https://docs.mubit.ai/guides/tasks-and-sessions)
- [Model IDs](https://docs.mubit.ai/guides/model-ids)
- [First adapter guide](https://docs.mubit.ai/guides/first-adapter)
- [Mubit key permissions](https://docs.mubit.ai/admin/mubit-keys)
- [Banyan Sapling](https://huggingface.co/MubitLabs/banyan-sapling)
- [Latte Bench](https://github.com/MubitLabs/latte-bench)
- User-provided workspace configuration and launch announcement.

Earlier landing-page-linked documentation paths returned 404:
`/getting-started`, `/patterns/outcome-attribution-loop`,
`/patterns/self-optimizing-prompts`, `/sdk/llm-providers`,
`/sdk/framework-integrations`, and `/api-reference/control-http`.
These were historical fetch results, not a fresh availability claim for every
path. Prefer the currently fetched gateway guides and ask Mubit for control docs.

Search results also surfaced older SDK material about Python/Node/Rust clients,
HTTP/gRPC transports, `recall`, outcome recording, agent definitions, and prompt
versioning. Those interfaces were not validated against this workspace and do
not establish an adapter-feedback, settings, or serving API. Minima examples
encountered in the research are not interchangeable with Mubit gateway APIs.

An earlier Selenium tool failure was worked around using local Chrome DOM
extraction for the landing page. That tool failure was not evidence of a Mubit
service outage. Public snippets, old SDK pages, and this configuration snapshot
must not override observed behavior or current workspace-specific documentation.
