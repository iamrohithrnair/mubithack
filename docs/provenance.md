# Provenance and disclosures

Last updated: 2026-10-24.

## Implementation

Precedenz combines a Strands agent, a constrained policy language, deterministic
replay and activation gates, and Banyan Sapling verification. The default hosted
model path is the Mubit gateway.

AI coding assistance was used during development. Architectural decisions, the
safety model, the policy language, the evaluation design, and the synthetic
dataset were specified and reviewed by the author.

## Dependencies

Standard, publicly available third-party dependencies are used and pinned in
`pyproject.toml`:

| Package | Version | Role |
| --- | --- | --- |
| `strands-agents` | 1.54.0 | the agent framework — central to the product |
| `openai` | 2.54.0 | Responses API client used by the Mubit provider |
| `fastapi` | 0.141.1 | HTTP + server-rendered views |
| `pydantic` | 2.13.5 | typed structured output and the policy schema |
| `jinja2` | 3.1.6 | templates |
| `python-multipart` | 0.0.32 | form posts |
| `uvicorn` | 0.52.4 | ASGI server |
| `boto3` | 1.43.88 | pulled in by the Bedrock provider |
| `psycopg[binary,pool]` | 3.3.5 | PostgreSQL backend (`postgres` extra) |
| `pytest`, `pytest-asyncio`, `httpx` | dev | tests |

## Data

**All data in this repository is synthetic and fabricated for the demo.**

- The company (Wharf Canary Optics), facility, personas, SKUs, serial numbers, order
  identifiers, customer references, inspection notes, and parts prices are invented.
- No real customer records, PII, private SOPs, credentials, confidential business data,
  or proprietary information appears anywhere in the repository or in the demo.
- Every "business system" the agent touches is a labeled synthetic adapter in
  `app/adapters/` backed by the app's own database (SQLite or PostgreSQL). None is a production integration, and the UI
  states this on every page.

## Reproducible checks

| Check | How to run it |
| --- | --- |
| Application safety regressions | `make test` uses a test-only model fake; no model credentials or hosted inference |
| Hosted model tool calls and typed output | `make smoke` uses the configured provider and consumes API credits |
| End-to-end hosted workflow | `make demo` uses the configured provider and synthetic business systems |
| Evaluation results | `make eval` counts outcomes from the database and writes `docs/evaluation-results.md` |
| PostgreSQL behavior | `make test-pg` adds PostgreSQL when the configured test server is reachable |

The fake in `tests/fake_model.py` is not a selectable application provider.
Offline regression results must not be presented as hosted model accuracy or
hosted inference latency.

## What is claimed, and what is not

**Claimed:** a real Strands agent doing real work end to end; a constrained policy
language; a working activation gate; replay-tested policy learning; measured evaluation results;
an append-only audit log with tamper detection.

**Not claimed:** production business-system integrations, automatic model
fine-tuning, an adapter serving API, or a universal plug-in for arbitrary domains.
The returns workflow is first-class in the UI; the accounts-payable domain
exercises the shared governance machinery through tests.

## Licenses

The application uses the standard Apache-2.0 license in `LICENSE`. The license's
publication date is part of its standard text, not the project's creation date.
Bundled fonts retain their OFL notice, and vendored JavaScript retains its
upstream license notices.
