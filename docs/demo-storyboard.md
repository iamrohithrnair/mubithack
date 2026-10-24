# Demo video: voiceover and storyboard

Extracted from the published demo, <https://youtu.be/25Cw8DO1J-0> (3:32, 1280×720, recorded
before the rename, so the video still says "OneDecision"). The voiceover comes from the
video's uploaded English caption track. The storyboard comes from frames sampled every
four seconds; timings are accurate to about ±2 seconds.

## Storyboard

| # | Time | On screen | Voiceover |
| --- | --- | --- | --- |
| 1 | 0:00–0:31 | Exception inbox. Scrolls to "Returns dock — cases not yet checked in" (CASE-2001 to 2006, each with a **Check in** button). The banner "No approved policy exists yet. Every case needs a person" is highlighted, with an empty board below (New · Investigating · Waiting for decision · Escalated · Resolved). | Intro: who it's for, Dana's problem, synthetic data. |
| 2 | 0:31–0:40 | Cursor clicks **Check in** on CASE-2001. A progress modal, "Investigating the check-in", ticks through: check-in received → asking the agent → reading the return case. | A dock check-in starts the work, not a chatbot. |
| 3 | 0:40–1:06 | CASE-2001 decision card, *Waiting for decision*. Headline: "Meridian 4100 kit returned complete except one $14 neoprene shoulder strap; serial matches, no damage, no policy covers it." The evidence table is highlighted, then the tool-name column (`compare_expected_and_received`, `lookup_replacement_cost`, …). Scrolls to Uncertainty and the highlighted **Proposed boundaries** list. | Four read-only tool calls; every line of evidence names its tool; the agent asks once and proposes the boundary. |
| 4 | 1:06–1:13 | Phone mockup (iPhone frame): Dana approves on mobile; "Turning your decision into a policy" progress. | Dana approves from her phone. |
| 5 | 1:13–1:34 | Proposed policy `returns.missing_accessory@v1` (CANDIDATE). The box "It may act only when all of these hold" (8 allowlisted conditions) is highlighted, then "And it may only do this" (set_disposition PARTS_HOLD, create_work_order max $25, close_exception). The diff table "What changes — v1 vs no active policy" shows below. | Constrained policy language, allowlist of nine fields, action set fixed by the system. |
| 6 | 1:34–1:46 | Scrolls to **Replay against 24 historical cases**, highlighted: 11 correctly automated · 13 correctly escalated · **0 wrongly actioned** · 100% coverage. "Replay passed" with an approval-token field and the **Activate this policy version** button. | Deterministic replay; zero wrong, or no activate button. |
| 7 | 1:46–1:58 | Token entered, activate clicked. Policies & audit page: `returns.missing_accessory@v1` **ACTIVE**, "Activated by dana.r@… ". | Only a human with a token activates; the model has no such tool. |
| 8 | 1:58–2:16 | Inbox: **Check in** CASE-2002. The board shows CASE-2002 under Resolved with an AUTO badge. CASE-2002's timeline highlights `action.executed` (disposition, work order), then `verification.passed`. | Body cap missing, policy matches, writes verified, closed against v1. Dana never saw it. |
| 9 | 2:16–2:37 | Inbox: **Check in** CASE-2003 → investigating modal → board shows CASE-2003 under **Escalated** in red ("serial number does not match"). CASE-2003 page: red box "Escalated — no automatic action was taken · serial number does not match the expected serial", with the timeline ending in `guardrail.blocked`. | Looks identical, wrong serial; the guardrail runs before any policy; ask a person. |
| 10 | 2:37–2:50 | Terminal titled "Amazon Bedrock AgentCore Runtime · us-west-2": `$ agentcore invoke "Investigate CASE-2001" --json`. JSON types out: `outcome: decision_requested`, `model_provider: bedrock`, four tool calls, decision-card headline and recommended action. "# response trimmed to the fields discussed". | Same agent on AgentCore, same tools and card, nothing executed. |
| 11 | 2:50–3:04 | Dashboard. KPI row highlighted: 3 cases handled · 2 automatic · 0 waiting · 1 escalated · 6 agent runs · 17 tool calls · 65.3K model tokens. Then "Cases by outcome" bars, "Tool calls by tool", and the token tile. | The dashboard counts the demo from the product's own records. |
| 12 | 3:04–3:15 | Policies & audit: scroll past the policy diff to the **Audit log** with the green badge "Hash chain intact (58 entries)" highlighted, showing entries from `guardrail.blocked` back to `event.received`. | Append-only, hash-chained log; 24/24 eval, zero false automatic actions. |
| 13 | 3:15–3:32 | End card. Logo "1 OneDecision" with "Teach the agent once. *It safely handles the next hundred decisions.*" Three-step flow: **New exception** "Camera kit returned missing its strap" [Approve and Teach] → **Policy v1** ✓ Replayed on 24 past cases ✓ Zero false automatic actions ✓ Activated by a person → **The next hundred**: a 10×10 grid of teal squares (handled) with one amber square (escalated). | Closing lines. |

Visual style: dark UI throughout, a teal highlight ring drawn around whatever the
voiceover is describing, the cursor always visible, and no cuts to a presenter.

## Voiceover

> **[0:00]** This is OneDecision, an agent built with Strands Agents, running on Claude Opus 5
> through Amazon Bedrock. It is for anyone who runs operations, where the same call lands on
> the same desk every week. Dana runs returns for a camera rental company. Twenty times a week,
> a kit comes back missing something small, and someone asks her what to do. She answers in
> thirty seconds, and she has never had an hour to write it down. Rule-based automation can't
> help, because nobody wrote the rule. Everything you'll see runs on synthetic data.
>
> **[0:31]** A kit is checked in at the returns dock, and that event is what starts the work,
> not somebody typing at a chatbot. The Strands agent investigates with four read-only tool
> calls. It pulls the case, reconciles what arrived against the bill of materials, prices the
> missing part, and checks for an approved policy. Every line of evidence names the tool that
> produced it. One neoprene strap missing, fourteen dollars. The serial matches and there's no
> damage, but nothing on file covers a case like this, so the agent stops and asks once, then
> proposes where the boundary should sit.
>
> **[1:06]** Dana isn't at her desk. She approves from her phone, between two other jobs, and
> that's the whole interruption.
>
> **[1:13]** Her approval is recorded, and that is all it does. The agent now proposes a
> policy, and it can only propose in a constrained language, where every condition has to come
> from an allowlist of nine fields. The action set is fixed by the system, not by the agent:
> holding for parts, raising a replacement work order under the spend cap, and closing the
> exception. Then deterministic code replays the candidate against twenty-four historical
> cases, and it would have gotten zero of them wrong. If that number were anything but zero,
> the activate button wouldn't be here.
>
> **[1:46]** A human activates it, and that's the only path from candidate to active. It takes
> a token, and it's refused without a passing replay. The model can't do it, because it isn't
> a tool the agent has.
>
> **[1:58]** Another kit comes in, missing a body cap. Same investigation, but this time an
> approved policy matches. The work order is raised, the disposition is set, both writes are
> read back and verified, and the exception is closed against policy version one. Dana never
> saw this case.
>
> **[2:16]** Now one that looks almost identical: same kit, one cheap accessory missing. But
> the serial doesn't match, so the wrong body came back, and the system refuses. A hard
> guardrail runs before any policy is consulted, and a policy can only ever narrow what's
> allowed. When the rules run out, the answer is to ask a person.
>
> **[2:37]** The same agent is deployed to Amazon Bedrock AgentCore Runtime. An invocation from
> AWS runs the same four tools on Bedrock and returns the same decision card, with nothing
> executed until a person approves.
>
> **[2:50]** The dashboard counts everything this demo just did, from the product's own
> records. Cases by outcome, the share handled automatically, the escalations, every agent run
> and tool call, and the tokens Bedrock reported.
>
> **[3:04]** Every event, tool call, approval, and action lands in one log. It only ever
> appends, each entry is hashed onto the one before it, and the database refuses to edit it.
> Across twenty-four evaluation cases, one hundred percent correct escalation, and zero false
> automatic actions. An agent that can act is easy. An agent someone will let act unattended
> is the one that ships. Teach the agent once. It safely handles the next hundred decisions.

About 640 words over 3:32, roughly 180 words per minute.
