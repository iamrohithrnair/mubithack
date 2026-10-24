# Integrating with Precedenz

Never answer the same question twice. Precedenz doesn't make decisions: it repeats yours. If it
can't check a case, it asks.

You send one case to a workstream and get one of two verdicts back:

- **`follow`**: a person already decided cases like this one. The receipt names the precedent
  and the bounded `actions` to carry out. Your code executes them. Precedenz executes nothing.
- **`ask`**: a person must decide. `reasons` says what differs or what could not be checked.
  A model, Sapling or tool failure is an ask with its reason, never an error.

There are four ways in, and they all use the same contract: HTTP, Python, the `precedenz` CLI,
and MCP for AI agents. They need two settings:

```bash
export PRECEDENZ_URL=http://127.0.0.1:8000          # `make run` serves here
export PRECEDENZ_API_TOKEN=replace-me-local-api-token # the local demo default
```

Answering a question, remembering an answer as a precedent, overruling a precedent and planting
a workstream are for a person only. They also need `PRECEDENZ_APPROVAL_TOKEN` (the local demo
default is `replace-me-local-demo-token`). The MCP server never reads it, so your agent can ask,
but only you can answer. Both defaults are placeholders: set real tokens for anything other than
a local demo.

## 1. HTTP

```bash
curl -s "$PRECEDENZ_URL/v1/decide" \
  -H "Authorization: Bearer $PRECEDENZ_API_TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Precedenz-Caller: pipeline" \
  -d '{"workstream": "expenses.meal_claim", "case_ref": "EXP-1042",
       "idempotency_key": "evt-88121",
       "facts": {"amount_usd": 62.4, "receipt_attached": true, "alcohol_only": false},
       "text": "Lunch with the Northwind buyer, receipt attached, $62.40"}'
```

`facts` and `text` apply only to planted workstreams. The built-in workstreams
(`returns.missing_accessory`, `ap.invoice_variance`) take only `case_ref` and re-derive
everything from their own systems. If you send the same `idempotency_key` with the same input,
you get the first decision back. If you send it with different input, you get 409.

| Route | Who | What it does |
| --- | --- | --- |
| `POST /v1/decide` | caller | Returns follow or ask for one case |
| `GET /v1/decisions/{id}` | caller | Returns the receipt, with its answers, outcomes and `resolved_by` |
| `GET /v1/decisions?verdict=ask&open=true&workstream=…` | caller | Lists the open questions |
| `POST /v1/decisions/{id}/outcome` | caller | Reports `{"status": "executed" or "failed", "detail": "…"}` |
| `GET /v1/workstreams`, `GET /v1/templates` | caller | Lists what exists and what you can plant |
| `POST /v1/decisions/{id}/answer` | person | Takes `{"answer", "remember", "up_to": {field: value}, "ignore": [field], "note"}` |
| `POST /v1/precedents/{id}/overrule` | person | Takes `{"reason"}` and stops the precedent at once |
| `POST /v1/workstreams` | person | Plants a workstream from `{"template_id"}` or `{"contract"}` |

Person routes also need the `X-Approval-Token: $PRECEDENZ_APPROVAL_TOKEN` header. The server
answers 401 for a bad API token, 403 for a missing approval token, 404 for an unknown workstream
or decision, 409 for an idempotency conflict, and 422 for a malformed body.

The live map has its own routes. They need no API token, because the local demo has no login:

- `GET /map/data` returns the whole map at rest.
- `GET /map/events` streams server-sent events. Each event has an `id:` (its seq) and a `data:`
  JSON line. To get events after a given seq, send `?since=<seq>` or a `Last-Event-ID` header.
  With neither, you get live events only.
- `GET /map/receipt/{id}` returns one receipt.
- `POST /map/answer/{id}` and `POST /map/overrule/{id}` are for a person. They take the
  approval token in the JSON body as `approval_token`.

## 2. Python

The client ships in this repository (`pip install -e .`). It needs only `httpx`, and it never
imports the server.

```python
import os
from precedenz_client import Precedenz, Conflict

p = Precedenz(os.environ["PRECEDENZ_URL"], api_token=os.environ["PRECEDENZ_API_TOKEN"],
              caller="pipeline")

receipt = p.decide("expenses.meal_claim", "EXP-1042",
                   facts={"amount_usd": 62.4, "receipt_attached": True, "alcohol_only": False},
                   text="Lunch with the Northwind buyer, receipt attached, $62.40")

if receipt["verdict"] == "follow":
    for action in receipt["actions"]:
        ...                                    # your executor; Precedenz executes nothing
    p.report_outcome(receipt["decision_id"], "executed")
else:
    print("a person decides:", receipt["reasons"])

for event in p.events(since=0):                # the map's live stream, as dicts
    print(event["type"], event.get("case_ref"))
```

The default idempotency key is derived from `case_ref`, so if you send a case twice, you get its
first decision back. A refusal raises a typed error: `AuthError` (401), `ApprovalRequired`
(403), `NotFound` (404), `Conflict` (409), `InvalidRequest` (400 or 422), `ServerError` (5xx) or
`Unreachable`. Each one is a `PrecedenzError` and carries the server's reason. Person calls
(`answer`, `overrule`, `plant`) need `approval_token=` on the client.

## 3. The CLI

```bash
precedenz workstreams
precedenz templates
precedenz plant expenses.meal_claim                               # person
precedenz decide expenses.meal_claim EXP-1042 --facts '{"amount_usd": 62.4}' --text "Team lunch"
precedenz decide returns.missing_accessory CASE-2002              # built-in: case_ref only
precedenz decide expenses.meal_claim EXP-1043 --facts @claim.json --key evt-88122
precedenz open --workstream expenses.meal_claim
precedenz receipt dec_…
precedenz answer dec_… approve --remember --up-to amount_usd=75 --ignore client_meeting   # person
precedenz overrule pol_… --reason "Too generous for weekend claims"                      # person
precedenz watch                                                   # live, colored tail of every decision
```

Global flags go before the command: `--url`, `--caller` (default `cli`), and `--json` to print
raw JSON instead of a card. If a token is unset, the CLI uses the local demo token and says so on
stderr. To send demo traffic, run `python scripts/demo_traffic.py --workstream expenses.meal_claim --count 8 --pace 1.5`.
It sends one case at a time, and `--max-model-calls N` keeps a live run within budget.

## 4. MCP, for AI agents

`precedenz-mcp` is a stdio MCP server with four tools: `decide`, `get_receipt`,
`list_open_questions` and `list_workstreams`. It has no tool that answers, remembers, overrules
or plants.

Claude Code:

```bash
claude mcp add precedenz -e PRECEDENZ_URL=http://127.0.0.1:8000 \
  -e PRECEDENZ_API_TOKEN=replace-me-local-api-token -- precedenz-mcp
```

If `precedenz-mcp` is not on your `PATH`, use its absolute path, for example
`$(pwd)/.venv/bin/precedenz-mcp`.

For Claude Desktop, add the server to `claude_desktop_config.json` (on macOS it is in
`~/Library/Application Support/Claude/`):

```json
{
  "mcpServers": {
    "precedenz": {
      "command": "/absolute/path/to/uniquedecision/.venv/bin/precedenz-mcp",
      "env": {
        "PRECEDENZ_URL": "http://127.0.0.1:8000",
        "PRECEDENZ_API_TOKEN": "replace-me-local-api-token"
      }
    }
  }
}
```

A `decide` result carries a `what_to_do` line. For a follow, it says to carry out exactly the
returned actions. For an ask, it says to tell the user what a person must decide, and why.

### A Strands tool

```python
import os
from strands import Agent, tool
from precedenz_client import Precedenz

precedenz = Precedenz(os.environ.get("PRECEDENZ_URL", "http://127.0.0.1:8000"),
                      api_token=os.environ["PRECEDENZ_API_TOKEN"], caller="agent")

@tool
def decide_case(workstream: str, case_ref: str, facts: dict | None = None,
                text: str | None = None) -> dict:
    """Ask Precedenz what to do with one case.

    `follow` repeats a decision a person already made: carry out exactly the returned
    actions. `ask` means a person must decide: tell the user what needs deciding and why.
    """
    return precedenz.decide(workstream, case_ref, facts=facts, text=text)

agent = Agent(tools=[decide_case])
```
