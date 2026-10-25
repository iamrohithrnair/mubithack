"""Send demo cases to a running Precedenz server, one at a time, and print each verdict.

    python scripts/demo_traffic.py --workstream expenses.meal_claim --count 8 --pace 1.5
    python scripts/demo_traffic.py --workstream returns.missing_accessory --count 9
    python scripts/demo_traffic.py --workstream ap.invoice_variance --max-model-calls 20

Sources: the returns demo cases (CASE-2001..2009) and the AP demo invoices
(AP-2001..2004) go by case_ref only, because built-in workstreams re-derive
their facts from the source system; a template's incoming examples
(fixtures/workstreams/<id>.json) go with their facts and text.

Each case carries one idempotency key, so a re-run repeats the same decisions
instead of making new ones. Sequential on purpose: SQLite has one writer, and a
decision holds it across its model call. `--max-model-calls` stops the run once
the receipts report that many gateway calls, which keeps a live run in budget.
It counts every receipt's calls, repeats included, so it never undercounts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # run from a checkout: use this checkout's client
    sys.path.insert(0, str(ROOT))

from precedenz_client.cli import DEMO_API_TOKEN, Paint  # noqa: E402
from precedenz_client.client import DEFAULT_URL, Precedenz, PrecedenzError, Unreachable  # noqa: E402

FIXTURES = ROOT / "fixtures"
BUILT_IN = {
    "returns.missing_accessory": ("cases.json", "demo_cases", "case_id"),
    "ap.invoice_variance": ("ap_invoices.json", "demo_invoices", "invoice_id"),
}


def load_cases(workstream: str, *, send: str = "both") -> list[dict[str, Any]]:
    """The demo cases for a workstream, in fixture order: {case_ref, facts?, text?}."""
    if workstream in BUILT_IN:
        filename, key, id_field = BUILT_IN[workstream]
        rows = json.loads((FIXTURES / filename).read_text())[key]
        return [{"case_ref": row[id_field]} for row in rows]
    path = FIXTURES / "workstreams" / f"{workstream}.json"
    if not path.exists():
        raise SystemExit(f"no demo cases for '{workstream}': {path} does not exist")
    cases = []
    for item in json.loads(path.read_text()).get("incoming", []):
        case: dict[str, Any] = {"case_ref": item["case_ref"]}
        if send in ("both", "facts") and item.get("facts") is not None:
            case["facts"] = item["facts"]
        if send in ("both", "text") and item.get("text"):
            case["text"] = item["text"]
        cases.append(case)
    return cases


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--workstream", default="expenses.meal_claim")
    p.add_argument("--count", type=int, default=8, help="how many cases to send (default 8)")
    p.add_argument("--start", type=int, default=0, help="skip this many cases first")
    p.add_argument("--pace", type=float, default=1.5, help="seconds between cases (default 1.5)")
    p.add_argument("--caller", default="script", help="caller label on the receipts")
    p.add_argument("--max-model-calls", type=int, default=None,
                   help="stop once the receipts report this many model calls")
    p.add_argument("--send", choices=("both", "facts", "text"), default="both",
                   help="for planted workstreams: send facts, text, or both (default)")
    p.add_argument("--url", default=None, help="server URL (default: $PRECEDENZ_URL or %s)" % DEFAULT_URL)
    p.add_argument("--dry-run", action="store_true", help="list the cases without sending them")
    return p


def _line(paint: Paint, n: int, total: int, case: str, receipt: dict[str, Any], seconds: float) -> str:
    verdict = str(receipt.get("verdict", "?")).lower()
    word = paint(verdict if verdict in ("follow", "ask") else "dim", f"{verdict.upper():<6}")
    if verdict == "follow":
        precedent = receipt.get("precedent") or {}
        detail = f"→ {precedent.get('id', '?')} v{precedent.get('version', '?')}"
        if receipt.get("resolves"):
            detail += f"  resolves {receipt['resolves']}"
    else:
        reasons = receipt.get("reasons") or ["no reason given"]
        detail = reasons[0][:64]
    calls = len(receipt.get("model_calls") or [])
    meta = paint("dim", f"{calls} call{'s' if calls != 1 else ''} · {seconds:.1f}s · {receipt.get('decision_id', '')}")
    return f"  {paint('dim', f'{n:>2}/{total}')}  {case:<12} {word} {detail:<44} {meta}"


def main(argv: list[str] | None = None, client: Precedenz | None = None,
         sleep=time.sleep) -> int:
    args = build_parser().parse_args(argv)
    paint = Paint()
    cases = load_cases(args.workstream, send=args.send)[args.start:args.start + args.count]
    if not cases:
        print(f"no cases to send for {args.workstream} from #{args.start}", file=sys.stderr)
        return 1
    if args.dry_run:
        for case in cases:
            print(json.dumps({"workstream": args.workstream, **case}))
        return 0
    if client is None:
        token = os.environ.get("PRECEDENZ_API_TOKEN", "").strip()
        if not token:
            print(paint("dim", "demo_traffic: PRECEDENZ_API_TOKEN is not set; using the local demo token"),
                  file=sys.stderr)
            token = DEMO_API_TOKEN
        url = args.url or os.environ.get("PRECEDENZ_URL", "").strip() or DEFAULT_URL
        client = Precedenz(url, api_token=token, caller=args.caller)

    budget = args.max_model_calls
    print(paint("bold", f"  {len(cases)} case{'s' if len(cases) != 1 else ''} → {args.workstream}")
          + paint("dim", f"  as '{args.caller}', every {args.pace:g}s"
                         + (f", budget {budget} model calls" if budget is not None else "")))
    tally = {"follow": 0, "ask": 0, "error": 0}
    used = 0
    for n, case in enumerate(cases, start=1):
        if budget is not None and used >= budget:
            print(paint("ask", f"  budget reached: {used} model calls; stopping before {case['case_ref']}"))
            break
        started = time.monotonic()
        try:
            receipt = client.decide(args.workstream, case["case_ref"], facts=case.get("facts"),
                                    text=case.get("text"))
        except Unreachable as exc:
            print(paint("bad", f"  {exc}"), file=sys.stderr)
            return 1
        except PrecedenzError as exc:
            tally["error"] += 1
            print(f"  {paint('dim', f'{n:>2}/{len(cases)}')}  {case['case_ref']:<12} "
                  + paint("bad", f"refused ({exc.status_code}): {exc}"))
        else:
            used += len(receipt.get("model_calls") or [])
            verdict = str(receipt.get("verdict", "")).lower()
            tally[verdict if verdict in tally else "error"] += 1
            print(_line(paint, n, len(cases), case["case_ref"], receipt, time.monotonic() - started),
                  flush=True)
        if n < len(cases) and args.pace > 0:
            sleep(args.pace)
    print(paint("dim", f"  {tally['follow']} follow · {tally['ask']} ask · {tally['error']} refused · "
                       f"{used} model calls"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
