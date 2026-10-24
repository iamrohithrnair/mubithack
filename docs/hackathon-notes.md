# Mubit Launch x Night Shift: hackathon reference

Compiled: 2026-10-24. This separates organizer text supplied by the user, earlier
event research, user preferences, and project planning. It is not a replacement
for organizer clarification.

Related: [Mubit research and settings](mubit-research.md) and
[Precedenz implementation plan](meta-harness-plan.md).

## 1. Event and launch information

| Item | Information gathered | Evidence |
| --- | --- | --- |
| Event | Night Shift: $65K Mubit Hackathon in London | User description and earlier event-listing research |
| Occasion | Launch of Mubit and an overnight/weekend hackathon | User-pasted launch announcement |
| Venue | WeWork, 30 Churchill Pl, Canary Wharf Estate, London E14 5RE | User-pasted announcement |
| Extra prizes | "$10K+ in prizes and more" announced | User-pasted announcement |
| Hiring | Mubit is hiring and will invite people met at the event to interview | User-pasted announcement |
| Winners | Direct interview with Mubit's co-founders | User-pasted announcement |
| Team repository | Repo URL captured when a team is created | Rule 6 |

Do not add $65K and $10K+ into a newly asserted total: the session did not verify
whether the headline already includes the increase, the prize breakdown, or
whether every prize is cash. An interview is not a guaranteed job offer.

The pasted launch message announced:

- **Banyan Sapling:** the first Banyan-family model, an open-weight research model
  verifying claims against policies and evidence and recognizing insufficient
  information. [Model page](https://huggingface.co/MubitLabs/banyan-sapling).
- **Latte Bench:** a simulated latte-art benchmark asking whether models learn
  from feedback, adjust their approach, and improve over attempts.
  [Repository](https://github.com/MubitLabs/latte-bench).
- **Mubit launch, additional prizes, and hiring/interview opportunities.**

"Tonight" and "this weekend" are relative wording from the supplied announcement,
not independently timestamped publication dates.

## 2. Rules supplied by the user

The six rules below preserve the supplied wording, including the ambiguity in
rule 4. The venue and numbered list were separated for readability.

> 1. All project code is written during the event, from 7:30 pm on Friday. Open-source libraries and your own tooling are fine.
>
> 2. Every project routes its LLM calls through Mubit.
>
> 3. Each team makes two check-ins, both on Saturday: the first by 12 pm and the second between 12 pm and 6 pm. A missed check-in can still be made late, until the code freeze.
>
> 4. Submissions open Sunday at 7am with `/submit`, and teams should submit by midnight. The code freeze, Sunday at 7 am, is the hard deadline for code and submissions: an upload after it counts only if an organizer accepts it. Until the freeze you can submit again, and your latest submission is the one that counts.
>
> 5. Submissions are uploads only: an MP4 or MOV video and a PDF deck. YouTube links are not accepted.
>
> 6. Your repo URL is captured when you create your team.

No additional official scoring weights, prize categories, maximum team size,
video-duration requirement, or judging-composition rule was established in the
supplied material.

## 3. Deadline interpretation and time zones

The environment date was Saturday 2026-10-24. Mapping the stated Friday/Saturday/
Sunday schedule onto that weekend gives the following dates, assuming the
organizers mean local London time:

| Milestone | Europe/London | UTC |
| --- | --- | --- |
| Project coding begins | Fri 2026-10-23, 19:30 BST | Fri 18:30 UTC |
| First check-in due | Sat 2026-10-24, 12:00 BST | Sat 11:00 UTC |
| Second check-in window | Sat 2026-10-24, 12:00-18:00 BST | Sat 11:00-17:00 UTC |
| Conservative hard code freeze | Sun 2026-10-25, 07:00 GMT | Sun 07:00 UTC |

London clocks go back during this weekend. At approximately 21:28 BST on
Saturday, the interval to 07:00 GMT Sunday was about 10 hours 32 minutes, not
9 hours 32 minutes. Local 01:xx occurs twice; use UTC or elapsed timers for
scheduling, and timezone-aware display.

Earlier assistant advice used Sunday 10:00 AM and a roughly 14-hour window.
That advice is superseded by the user's explicit 07:00 rule. The date table is
a calendar interpretation, not an independently verified event calendar.

### Rule 4 needs organizer clarification

The text says submissions open at 07:00 Sunday, says "by midnight," and also
makes 07:00 Sunday the hard submission deadline. These cannot be resolved into
a reliable upload window from the text alone.

Conservative action:

1. Freeze code and prepare final artifacts before 07:00 Sunday.
2. Confirm with organizers when `/submit` actually accepts uploads and the
   applicable upload deadline. Do not assume an early-morning upload is possible
   if the portal is not open.
3. Do not rely on "midnight" as an extension of code freeze.
4. An upload after the stated hard stop counts only with organizer acceptance.
5. Preserve any explicit clarification; do not imply an exception was granted
   merely because it was requested.

Late check-ins are expressly allowed until code freeze. The session did not
verify that either team check-in had actually been completed.

## 4. Submission artifacts and the user's preference

The supplied rule requires uploaded MP4/MOV video and PDF deck, not a YouTube
link. The latest valid resubmission before the applicable cutoff is said to count.

The user explicitly said **no PDF deck is needed** and requested no deck work.
No deck has been produced as part of this documentation task. An organizer
waiver was not recorded, so distinguish that project preference from the text
of rule 5 rather than quietly changing the rule.

For the video:

- Record actual product behavior and the corresponding Mubit evidence.
- Verify that the exported file plays locally and is uploadable as MP4 or MOV.
- Do not treat an old external demo link as an accepted submission.
- Keep material ready with time for export/upload failures and organizer checks.

There is no verified mandatory two-minute or 3:32 duration in the supplied rules.
Those durations appeared in proposed/demo scripts, not as event requirements.

## 5. Mubit alignment and judging assumptions

Earlier event-listing research described the theme as routing recurring LLM work
through Mubit and turning corrections into model updates. Discussions included
recurring tasks, closed-to-open comparisons with quality/cost/latency, and fast
refresh. Exact track labels, eligibility conditions, and scoring weights were
not confirmed by the six pasted rules.

Keep these separate:

- **Explicit rule:** every project routes its LLM calls through Mubit.
- **Event/product theme:** recurring tasks improve from experience and feedback.
- **Proposed evidence:** actual request IDs, task grouping, version progress,
  held-out quality, latency, and cost per successful task.
- **Not an established rule:** a particular adapter API, minimum number of calls,
  automatic live rollout by a specific hour, or an AI-majority judging panel.

AI-assisted review was discussed, but the supplied organizer text does not
establish who reviews submissions or how an AI would score them. Write a clear,
evidence-backed submission for any reviewer; do not invent judging requirements.

Launching Sapling and Latte Bench makes them relevant context, not mandatory
integrations under the supplied rules. Explain how a component helps the
workflow rather than name-dropping it.

## 6. Current Precedenz direction

Current product choice: an integration-first decision meta-harness, not a new
model family or mainly an AI review tool.

- Existing customer pipelines keep their source systems and executor.
- Precedenz supplies typed decisions, policy boundaries, safe escalation, and
  receipts; Mubit owns supported model training and native promotion.
- Preserve the existing recurring task fingerprint so current examples remain
  useful. Add a thin HTTP API and client rather than rewriting the whole app.
- A model upgrade must not increase authority to perform business actions.
- Human approvals/corrections remain distinct from AI verification and from
  actual model-weight updates.

Messaging explored in the conversation:

- "Never answer the same question twice."
- "It doesn't make decisions. It repeats yours."
- "If it can't check it, it asks."
- "Your pipeline stays. The model can improve. Its authority does not grow."

These are product positioning, not guarantees of zero errors or zero future
human questions.

The "Vacation Test" and its illustrative 200 cases / 187 handled / 13 waiting
were a proposed story, not measured results. "One correction, two updates"
described an ambition: a governed policy update plus a verified Mubit learning
update. The latter must not be claimed until the correction mechanism is proven.

## 7. Available evidence and caveats

- The user reports adapters are enabled and calls/traces are already captured.
- A read-only authenticated gateway model-list request succeeded in this session.
  That proves connectivity, not actual trained-version serving.
- The supplied workspace settings enable training and automatic promotion, but
  add shadow, canary, judged-case, pair, and session requirements.
- Training after 25 kept examples does not guarantee immediate live rollout.
- Automatic serving eligibility and actual version correlation remain to be
  confirmed for the current Responses/tool/streaming route.
- The shipped app teaches explicit policies. A correction-to-model feedback API
  was not verified; the supplied snapshot has `feedback.outcome=off`.
- The 24-case offline report and last 213-passing-test result are application
  regression evidence, not Mubit inference accuracy or latency measurements.

Do not manufacture adapter versions, session traffic, cost savings, or a passing
quality comparison. If rollout is still collecting, training, or testing at the
deadline, show that actual state.

## 8. Code timing, licensing, and repository

The user explained that the initial commit was delayed and that generic license
and `.github` files were already present. A large first commit or filesystem
timestamp does not independently establish when application code was authored.
No pre-event-code finding was verified; an earlier assistant inference to that
effect was unwarranted.

The Apache license text was preserved unchanged. Its "January 2004" date is the
standard license version, not the project's creation date. Refreshing a local
modification time is not a change to Git history or authorship.

The requested cleanup removed obsolete deployment/demo material, `.github`,
the shipped scripted provider, and the deployment-specific runtime. Test-only
fakes remain for offline checks. No history rewrite was performed.

Known pushed code commit: `be95704`, message `cleanup and refactor`, on `main`.
Current project remote: [iamrohithrnair/mubithack](https://github.com/iamrohithrnair/mubithack).
This does not verify which URL was registered with the team; check that it is
the same repository. New planning/reference documents are not automatically a
submitted artifact or a newly pushed commit.

Rule 1 still governs eligibility. If tooling/library boundaries need clarification,
ask organizers about the actual facts; do not fabricate an earlier-work disclosure,
an event-timing claim, or an organizer exception.

## 9. Remaining operational checks

- Confirm both check-ins and any required late check-in procedure.
- Confirm `/submit` opening and the contradictory upload-deadline wording.
- Resolve the PDF preference versus rule 5 if an exception is intended.
- Verify the registered repository matches the remote used for submission.
- Confirm native adapter serving, serving-version evidence, and supported controls.
- Set a budget before generating any additional paid traffic.
- Finish/test the bounded integration path before adding extra domains or UI.
- Record real behavior; export and locally verify the uploadable video.

The [implementation plan](meta-harness-plan.md) budgets approximately 6 h 50 min
for capability checks, the API, trace correlation, two caller workflows, outcomes,
tests, and recording. That is a proposed budget, not a record of work completed
or a promise that Mubit training will finish within it.

Sources: the user's launch announcement and six rules, earlier event research,
current session observations, [Mubit](https://mubit.ai/), and the linked Sapling
and Latte Bench releases. Where the information is contradictory or unverified,
the uncertainty is intentionally retained.
