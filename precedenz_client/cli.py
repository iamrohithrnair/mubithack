"""`precedenz`: the terminal way in.

    precedenz decide expenses.meal_claim EXP-1042 --facts '{"amount_usd": 62.4}'
    precedenz open
    precedenz answer dec_… approve --remember --up-to amount_usd=75 --ignore client_meeting
    precedenz plant calendar.meeting_invite
    precedenz watch

Environment: PRECEDENZ_URL (default http://127.0.0.1:8000), PRECEDENZ_API_TOKEN,
PRECEDENZ_APPROVAL_TOKEN. An unset API token falls back to the server's shipped
local demo token, and the CLI says so on stderr. The approval token never falls
back: answer, overrule and plant are a person's decisions, so they need
PRECEDENZ_APPROVAL_TOKEN set, or a person typing it at an interactive prompt.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import sys
import textwrap
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from precedenz_client.client import DEFAULT_URL, Precedenz, PrecedenzError, Unreachable

DEMO_API_TOKEN = "replace-me-local-api-token"
DEMO_APPROVAL_TOKEN = "replace-me-local-demo-token"

# ----------------------------------------------------------------- styling

_STYLES = {
    "follow": "1;38;5;51",      # cyan
    "ask": "1;38;5;214",        # amber
    "decide": "1;97",           # white
    "overrule": "1;38;5;203",   # red
    "model": "2;38;5;141",      # dim violet
    "precedent": "1;38;5;87",   # bright cyan: a decision was made once
    "plant": "1;38;5;114",      # green
    "ok": "38;5;114",
    "bad": "38;5;203",
    "dim": "2",
    "bold": "1",
    "label": "38;5;245",
    "quote": "3",
}
_PILLS = {"follow": "1;38;5;16;48;5;51", "ask": "1;38;5;16;48;5;214",
          "overrule": "1;38;5;16;48;5;203", "plant": "1;38;5;16;48;5;114",
          "precedent": "1;38;5;16;48;5;87", "decide": "1;38;5;16;48;5;255"}


def _color_enabled(stream=None) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR") or os.environ.get("PRECEDENZ_COLOR") == "1":
        return True
    stream = stream or sys.stdout
    return hasattr(stream, "isatty") and stream.isatty()


class Paint:
    """ANSI styling that disappears when the output is not a terminal."""

    def __init__(self, enabled: bool | None = None):
        self.enabled = _color_enabled() if enabled is None else enabled

    def __call__(self, style: str, text: Any) -> str:
        text = str(text)
        code = _STYLES.get(style, style)
        return f"\033[{code}m{text}\033[0m" if self.enabled and code else text

    def pill(self, kind: str, word: str) -> str:
        if not self.enabled:
            return word
        return f"\033[{_PILLS.get(kind, _PILLS['decide'])}m {word} \033[0m"


def _width() -> int:
    """The card's width: the terminal's, kept compact enough to read at a glance."""
    return max(60, min(shutil.get_terminal_size((88, 24)).columns, 88))


LABEL_W = 11


def _row(paint: Paint, label: str, lines: Iterable[str]) -> list[str]:
    out = []
    for i, line in enumerate(lines):
        head = paint("label", f"{label:<{LABEL_W}}") if i == 0 else " " * LABEL_W
        out.append(f"  {head}{line}")
    return out


def _wrap(text: str, indent: int = 2 + LABEL_W) -> list[str]:
    return textwrap.wrap(str(text), width=max(30, _width() - indent)) or [""]


def _person(email: str | None) -> str:
    if not email:
        return "a person"
    local, at, _ = email.partition("@")
    if not at:
        return email
    parts = [p for p in local.replace("-", ".").replace("_", ".").split(".") if p]
    if len(parts) >= 2:
        return f"{parts[0].capitalize()} {parts[1][0].upper()}."
    return parts[0].capitalize() if parts else email


def _money(value: Any) -> str:
    return f"{value:.2f}" if isinstance(value, float) else str(value)


def _action_text(action: dict[str, Any]) -> str:
    kind = action.get("type", "action")
    params = [f"{k} {_money(v)}" for k, v in action.items() if k != "type"]
    if kind == "answer" and "answer" in action:
        rest = [f"{k} {_money(v)}" for k, v in action.items() if k not in ("type", "answer")]
        return f"answer {action['answer']}" + (f" · {' · '.join(rest)}" if rest else "")
    return kind + (f" · {' · '.join(params)}" if params else "")


def _clock(at: str | None) -> str:
    if not at:
        return "--:--:--"
    try:
        return datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
    except ValueError:
        return at[11:19] if len(at) >= 19 else at


# ------------------------------------------------------------- the cards


def verdict_card(receipt: dict[str, Any], paint: Paint | None = None) -> str:
    """The receipt as a compact card: verdict, case, precedent, reasons, checks, Mubit IDs."""
    paint = paint or Paint()
    verdict = str(receipt.get("verdict", "?")).lower()
    word = {"follow": "FOLLOW", "ask": "ASK"}.get(verdict, verdict.upper())
    style = verdict if verdict in ("follow", "ask") else "decide"
    case = f"{receipt.get('workstream', '?')} · {receipt.get('case_ref', '?')}"
    head = f" {paint.pill(style, word)}  {paint('bold', case)}"
    if not paint.enabled:
        head = f" {word}  {case}"
    lines = [head, " " + paint(style, "─" * (_width() - 2))]

    precedent = receipt.get("precedent") or None
    if verdict == "ask":
        reasons = receipt.get("reasons") or ["no reason given"]
        why = []
        for reason in reasons:
            wrapped = _wrap(reason, 2 + LABEL_W + 2)
            why.append(paint("ask", "• ") + wrapped[0])
            why.extend("  " + w for w in wrapped[1:])
        lines += _row(paint, "why", why)
    if precedent:
        version = precedent.get("version")
        text = precedent.get("text") or precedent.get("name") or precedent.get("id", "")
        tag = f"v{version}  " if version is not None else ""
        body = _wrap(f'{tag}"{text}"')
        body = [paint("quote", body[0])] + [paint("quote", b) for b in body[1:]]
        who = [f"decided by {_person(precedent.get('decided_by'))}"]
        if precedent.get("origin_case"):
            who.append(f"on {precedent['origin_case']}")
        body.append(paint("dim", " ".join(who) + (f" · {precedent['id']}" if precedent.get("id") else "")))
        lines += _row(paint, "precedent" if verdict == "follow" else "nearest", body)
    actions = receipt.get("actions") or []
    if actions:
        lines += _row(paint, "actions", [_action_text(a) for a in actions if isinstance(a, dict)])
    checks = receipt.get("checks") or []
    if checks:
        rendered = []
        room = _width() - (2 + LABEL_W)
        for check in checks:
            mark = paint("ok", "✓") if check.get("ok") else paint("bad", "✗")
            name, detail = str(check.get("name", "")), str(check.get("detail") or "")
            if detail and len(name) + len(detail) + 4 <= room:
                rendered.append(f"{mark} {name}  {paint('dim', detail)}")
                continue
            rendered.append(f"{mark} {name}")
            # A long detail goes underneath, wrapped, so the card keeps its edge.
            rendered.extend("  " + paint("dim", w) for w in textwrap.wrap(detail, max(30, room - 2)))
        lines += _row(paint, "checks", rendered)
    calls = receipt.get("model_calls") or []
    if calls:
        rendered = []
        for call in calls:
            rid = call.get("request_id") or "request id not reported"
            bits = [rid, call.get("requested_model"), f"{call['ms']} ms" if call.get("ms") is not None else None]
            rendered.append(paint("model", " · ".join(str(b) for b in bits if b)))
        if receipt.get("serving_identity"):
            rendered.append(paint("dim", f"serving: {receipt['serving_identity']}"))
        lines += _row(paint, "mubit", rendered)
    if receipt.get("resolves"):
        lines += _row(paint, "resolves", [f"{receipt['resolves']} " + paint("dim", "(a waiting question)")])
    for answer in receipt.get("answers") or []:
        remembered = " · remembered" if answer.get("remembered") else ""
        lines += _row(paint, "answered", [
            f"{answer.get('answer')}{remembered} " + paint("dim", f"by {_person(answer.get('actor'))}")
        ])
    for outcome in receipt.get("outcomes") or []:
        lines += _row(paint, "outcome", [f"{outcome.get('status')} " + paint("dim", outcome.get("detail") or "")])
    if receipt.get("resolved_by"):
        lines += _row(paint, "resolved", [f"by {receipt['resolved_by']}"])
    lines += _row(paint, "receipt", [paint("dim", receipt.get("decision_id", "?"))])
    if verdict == "ask" and not receipt.get("answers") and not receipt.get("resolved_by"):
        hint = f"precedenz answer {receipt.get('decision_id', '<id>')} <answer> --remember"
        lines += _row(paint, "next", [f"a person decides  {paint('dim', hint)}"])
    return "\n".join(lines)


def answer_card(result: dict[str, Any], decision_id: str, answer: str, remember: bool,
                paint: Paint | None = None) -> str:
    paint = paint or Paint()
    precedent = result.get("precedent") if isinstance(result, dict) else None
    head_word = "DECIDED ONCE" if precedent else "ANSWERED"
    style = "precedent" if precedent else "decide"
    head = f" {paint.pill(style, head_word)}  {paint('bold', answer)}  {paint('dim', decision_id)}"
    if not paint.enabled:
        head = f" {head_word}  {answer}  {decision_id}"
    lines = [head, " " + paint(style, "─" * (_width() - 2))]
    if precedent:
        text = precedent.get("text") or precedent.get("name") or ""
        body = [paint("quote", w) for w in _wrap(f'v{precedent.get("version", "?")}  "{text}"')]
        body.append(paint("dim", f"{precedent.get('id', '')} · {precedent.get('status', 'active')}"))
        lines += _row(paint, "precedent", body)
    elif remember:
        lines += _row(paint, "precedent", [paint("ask", "not remembered: nothing became a precedent")])
    replay = result.get("replay") if isinstance(result, dict) else None
    if isinstance(replay, dict) and replay:
        bits = []
        if replay.get("cases_replayed") is not None:
            bits.append(f"{replay['cases_replayed']} past answers")
        bad = replay.get("contradictions", replay.get("false_automatic_actions"))
        if isinstance(bad, list):
            bad = len(bad)
        if bad is not None:
            bits.append(f"{bad} contradictions")
        if "passed" in replay:
            bits.append(paint("ok", "passed") if replay["passed"] else paint("bad", "blocked"))
        lines += _row(paint, "replay", [" · ".join(bits) or json.dumps(replay)])
    resolved = result.get("resolved") if isinstance(result, dict) else None
    if resolved:
        many = len(resolved) != 1
        rows = [f"{len(resolved)} waiting question{'s' if many else ''} "
                f"resolved on {'their' if many else 'its'} own"]
        for item in resolved:
            if isinstance(item, dict):
                verdict = str(item.get("verdict", "follow")).lower()
                word = paint(verdict if verdict in ("follow", "ask") else "dim", verdict.upper())
                rows.append(f"{item.get('case_ref', '?'):<14} {word}  {paint('dim', item.get('decision_id', ''))}")
            else:
                rows.append(paint("dim", str(item)))
        lines += _row(paint, "resolved", rows)
    elif precedent:
        lines += _row(paint, "resolved", [paint("dim", "no waiting question matched it")])
    return "\n".join(lines)


def workstream_card(ws: dict[str, Any], paint: Paint | None = None, word: str = "PLANTED") -> str:
    paint = paint or Paint()
    ws = ws.get("workstream") if isinstance(ws.get("workstream"), dict) else ws
    head = f" {paint.pill('plant', word)}  {paint('bold', ws.get('id', '?'))}  {ws.get('label', '')}"
    if not paint.enabled:
        head = f" {word}  {ws.get('id', '?')}  {ws.get('label', '')}"
    lines = [head, " " + paint("plant", "─" * (_width() - 2))]
    if ws.get("question"):
        lines += _row(paint, "question", _wrap(ws["question"]))
    meta = [ws.get("audience"), f"owner {ws['owner']}" if ws.get("owner") else None,
            "reader on" if ws.get("reader") else None]
    if any(meta):
        lines += _row(paint, "audience", [" · ".join(m for m in meta if m)])
    fields = ws.get("fields") or []
    if fields:
        rendered = []
        for f in fields:
            ceiling = f" ≤ {_money(f['ceiling'])}" if f.get("ceiling") is not None else ""
            kind = "(" + str(f.get("kind")) + ceiling + ")"
            rendered.append(f"{f.get('name')} {paint('dim', kind)}")
        lines += _row(paint, "fields", rendered)
    if ws.get("repeatable_answer"):
        never = ws.get("other_answers") or []
        tail = paint("dim", f"   never repeats: {', '.join(never)}") if never else ""
        lines += _row(paint, "repeats", [f"{ws['repeatable_answer']}{tail}"])
    return "\n".join(lines)


def event_line(event: dict[str, Any], paint: Paint | None = None) -> str:
    """One line per event for `precedenz watch`."""
    paint = paint or Paint()
    kind = event.get("type", "")
    clock = paint("dim", _clock(event.get("at")))
    case = f"{event.get('workstream', '')} · {event.get('case_ref', '')}"

    def line(style: str, word: str, body: str, tail: str = "") -> str:
        return f"{clock}  {paint(style, f'{word:<9}')} {body}" + (f"  {paint('dim', tail)}" if tail else "")

    if kind == "decision.started":
        return line("decide", "DECIDE", case, event.get("caller", ""))
    if kind == "model.call":
        bits = [event.get("request_id") or "request id not reported", event.get("requested_model"),
                f"{event['ms']} ms" if event.get("ms") is not None else None]
        return paint("model", f"{_clock(event.get('at'))}  {'  model':<9} {case}  "
                              + " · ".join(str(b) for b in bits if b))
    if kind == "decision.finished":
        verdict = str(event.get("verdict", "")).lower()
        if verdict == "follow":
            tail = f"resolves {event['resolves']}" if event.get("resolves") else (event.get("caller") or "")
            body = case + (f"  {paint('follow', '→')} {event['precedent_id']}" if event.get("precedent_id") else "")
            return line("follow", "FOLLOW", body, tail)
        reasons = event.get("reasons") or []
        reason = reasons[0] if reasons else ""
        return line("ask", "ASK", f"{case}  {reason}", event.get("caller") or "")
    if kind == "precedent.decided":
        text = event.get("text") or ""
        return line("precedent", "PRECEDENT",
                    f"{event.get('workstream', '')} v{event.get('version', '?')}  \"{text}\"",
                    f"by {_person(event.get('decided_by'))}")
    if kind == "precedent.overruled":
        return line("overrule", "OVERRULE", f"{event.get('precedent_id', '')}  {event.get('reason', '')}",
                    f"by {_person(event.get('by'))}")
    if kind == "workstream.planted":
        return line("plant", "PLANT", f"{event.get('workstream', '')}  {event.get('label', '')}",
                    event.get("audience", ""))
    if kind == "case.answered":
        remembered = " · remembered" if event.get("remembered") else ""
        return line("decide", "ANSWER", f"{event.get('decision_id', '')}  {event.get('answer', '')}{remembered}")
    if kind == "outcome.reported":
        return line("dim", "OUTCOME", f"{event.get('decision_id', '')}  {event.get('status', '')}")
    return line("dim", kind or "event", json.dumps({k: v for k, v in event.items() if k not in ("seq", "at", "type")}))


# ------------------------------------------------------------- arguments


def _json_arg(raw: str) -> Any:
    """JSON inline, `@file`, or `@-` for stdin."""
    if raw.startswith("@"):
        source = raw[1:]
        raw = sys.stdin.read() if source == "-" else Path(source).expanduser().read_text()
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not valid JSON: {exc}") from exc


def _pair(raw: str) -> tuple[str, Any]:
    name, sep, value = raw.partition("=")
    if not sep or not name:
        raise argparse.ArgumentTypeError(f"expected field=value, got '{raw}'")
    try:
        parsed: Any = float(value)
        if parsed.is_integer() and "." not in value:
            parsed = int(parsed)
    except ValueError:
        parsed = {"true": True, "false": False}.get(value.lower(), value)
    return name.strip(), parsed


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="precedenz",
        description="Never answer the same question twice. It doesn't make decisions. "
                    "It repeats yours. If it can't check it, it asks.",
    )
    p.add_argument("--url", default=None, help="server URL (default: $PRECEDENZ_URL or %s)" % DEFAULT_URL)
    p.add_argument("--caller", default="cli", help="caller label recorded on receipts (default: cli)")
    p.add_argument("--json", action="store_true", help="print the raw JSON instead of a card")
    # The same flags after the command, too; SUPPRESS keeps an absent one from
    # overwriting what was given before the command.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--url", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--caller", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="print the raw JSON instead of a card")
    sub = p.add_subparsers(dest="command", required=True, metavar="command")

    def command(name: str, help: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help, parents=[common])

    d = command("decide", help="follow a precedent or ask a person, for one case")
    d.add_argument("workstream")
    d.add_argument("case_ref")
    d.add_argument("--facts", type=_json_arg, help="typed facts: JSON, @file, or @- for stdin")
    d.add_argument("--text", help="the case as written, for workstreams with a reader")
    d.add_argument("--key", help="idempotency key (default: derived from the case)")
    d.add_argument("--revision", help="source revision of the case")

    r = command("receipt", help="show one decision's receipt")
    r.add_argument("decision_id")

    o = command("open", help="list the questions waiting for a person")
    o.add_argument("--workstream")

    a = command("answer", help="answer a waiting question (needs the approval token)")
    a.add_argument("decision_id")
    a.add_argument("answer")
    a.add_argument("--remember", action="store_true", help="make this answer a precedent")
    a.add_argument("--up-to", dest="up_to", type=_pair, nargs="+", action="extend", default=[],
                   metavar="FIELD=VALUE", help="widen an amount up to this value")
    a.add_argument("--ignore", type=str, nargs="+", action="extend", default=[], metavar="FIELD",
                   help="fields that don't matter for this answer")
    a.add_argument("--note", default="")

    v = command("overrule", help="stop a precedent at once (needs the approval token)")
    v.add_argument("precedent_id")
    v.add_argument("--reason", required=True)

    pl = command("plant", help="plant a workstream from a template (needs the approval token)")
    pl.add_argument("template", nargs="?")
    pl.add_argument("--contract", type=_json_arg, help="a contract instead: JSON or @file")

    command("workstreams", help="list the workstreams")
    command("templates", help="list the templates you can plant")

    w = command("watch", help="live, colored tail of every decision")
    w.add_argument("--since", type=int, default=None, help="replay from this event seq (default: now)")
    return p


# ---------------------------------------------------------------- running

PERSON_COMMANDS = {"answer", "overrule", "plant"}


def _token(name: str, demo: str, note: Callable[[str], None]) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    note(f"{name} is not set: using the local demo token. Set it for anything but a local demo.")
    return demo


class ApprovalRequired(Exception):
    """A person-only command ran without a person's approval token."""


def _approval_token() -> str:
    """The approval token, from the environment or typed by a person. Never a default.

    Answering, overruling and planting are a person's decisions. Falling back to
    the shipped demo token would let anything with a shell (an AI agent
    included) make them, so the token must be set explicitly, or typed at an
    interactive prompt.
    """
    value = os.environ.get("PRECEDENZ_APPROVAL_TOKEN", "").strip()
    if value:
        return value
    if hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
        try:
            typed = getpass.getpass("precedenz: approval token (only a person answers): ").strip()
        except (EOFError, KeyboardInterrupt):
            typed = ""
        if typed:
            return typed
    raise ApprovalRequired(
        "answer, overrule and plant are a person's decisions: set PRECEDENZ_APPROVAL_TOKEN, "
        "or run the command in a terminal and type the approval token"
    )


def make_client(args: argparse.Namespace) -> Precedenz:
    """The client for this invocation, from flags and the environment."""
    err = Paint(_color_enabled(sys.stderr))

    def note(msg: str) -> None:
        print(err("dim", f"precedenz: {msg}"), file=sys.stderr)

    url = args.url or os.environ.get("PRECEDENZ_URL", "").strip() or DEFAULT_URL
    quiet = args.command == "watch"
    api_token = _token("PRECEDENZ_API_TOKEN", DEMO_API_TOKEN, (lambda m: None) if quiet else note)
    approval = None
    if args.command in PERSON_COMMANDS:
        approval = _approval_token()
    return Precedenz(url, api_token=api_token, approval_token=approval, caller=args.caller)


def _print_json(body: Any) -> None:
    print(json.dumps(body, indent=2, default=str))


def _table(paint: Paint, headers: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    last = len(headers) - 1
    out = ["  " + "  ".join(paint("label", h.upper() if i == last else h.upper().ljust(w))
                            for i, (h, w) in enumerate(zip(headers, widths)))]
    for row in rows:
        out.append("  " + "  ".join(cell if i == last else cell.ljust(w)
                                    for i, (cell, w) in enumerate(zip(row, widths))))
    return "\n".join(out)


def run(args: argparse.Namespace, client: Precedenz, paint: Paint) -> int:
    cmd = args.command
    if cmd == "decide":
        receipt = client.decide(args.workstream, args.case_ref, idempotency_key=args.key,
                                facts=args.facts, text=args.text, source_revision=args.revision)
        _print_json(receipt) if args.json else print(verdict_card(receipt, paint))
    elif cmd == "receipt":
        receipt = client.receipt(args.decision_id)
        _print_json(receipt) if args.json else print(verdict_card(receipt, paint))
    elif cmd == "open":
        questions = client.open_questions(args.workstream)
        if args.json:
            _print_json(questions)
        elif not questions:
            print(paint("dim", "  nothing is waiting for a person"))
        else:
            print(paint("ask", f"  {len(questions)} question{'s' if len(questions) != 1 else ''} "
                               "waiting for a person"))
            rows = [[q.get("decision_id", "?"), f"{q.get('workstream', '?')} · {q.get('case_ref', '?')}",
                     ((q.get("reasons") or [""])[0])[:60]] for q in questions]
            print(_table(paint, ["decision", "case", "why"], rows))
    elif cmd == "answer":
        result = client.answer(args.decision_id, args.answer, remember=args.remember,
                               up_to=dict(args.up_to) or None, ignore=args.ignore or None, note=args.note)
        if args.json:
            _print_json(result)
        else:
            print(answer_card(result or {}, args.decision_id, args.answer, args.remember, paint))
    elif cmd == "overrule":
        result = client.overrule(args.precedent_id, args.reason)
        if args.json:
            _print_json(result)
        else:
            head = (f" {paint.pill('overrule', 'OVERRULED')}  {paint('bold', args.precedent_id)}"
                    if paint.enabled else f" OVERRULED  {args.precedent_id}")
            print(head)
            print(" " + paint("overrule", "─" * (_width() - 2)))
            print("\n".join(_row(paint, "reason", _wrap(args.reason))))
            print("\n".join(_row(paint, "now", ["it stops at once: its kind of question asks again"])))
    elif cmd == "plant":
        if not args.template and not args.contract:
            raise PrecedenzError("plant needs a template id (see `precedenz templates`) or --contract")
        result = client.plant(args.template, contract=args.contract)
        _print_json(result) if args.json else print(workstream_card(result or {}, paint))
    elif cmd == "workstreams":
        items = client.workstreams()
        if args.json:
            _print_json(items)
        else:
            rows = [[w.get("id", "?"), w.get("kind", ""), w.get("audience", ""),
                     str(w.get("repeatable_answer", ""))[:28], w.get("label", "")] for w in items]
            print(_table(paint, ["workstream", "kind", "audience", "repeats", "label"], rows))
    elif cmd == "templates":
        items = client.templates()
        if args.json:
            _print_json(items)
        else:
            rows = []
            for t in items:
                c = t.get("contract") if isinstance(t.get("contract"), dict) else t
                rows.append([t.get("id") or c.get("id", "?"), c.get("audience", ""), c.get("label", "")])
            print(_table(paint, ["template", "audience", "label"], rows))
            print(paint("dim", "\n  plant one:  precedenz plant <template>"))
    elif cmd == "watch":
        return watch(client, paint, since=args.since, raw=args.json)
    return 0


def watch(client: Precedenz, paint: Paint, *, since: int | None = None, raw: bool = False,
          max_events: int | None = None) -> int:
    """Follow /map/events, reconnecting with the last seen seq when the stream drops."""
    print(paint("dim", f"  precedenz watch · {client.url} · live · Ctrl-C to stop"))
    seen = 0
    last = since
    backoff = 1.0
    try:
        while True:
            try:
                for event in client.events(since=last):
                    backoff = 1.0
                    last = event.get("seq", last)
                    print(json.dumps(event) if raw else event_line(event, paint), flush=True)
                    seen += 1
                    if max_events is not None and seen >= max_events:
                        return 0
            except Unreachable as exc:
                print(paint("dim", f"  stream lost ({exc}); reconnecting in {backoff:.0f}s"), file=sys.stderr)
            time.sleep(backoff)
            backoff = min(backoff * 2, 10.0)
    except KeyboardInterrupt:
        return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paint = Paint()
    err = Paint(_color_enabled(sys.stderr))
    try:
        client = make_client(args)
    except ApprovalRequired as exc:
        print(err("bad", f"precedenz: {exc}"), file=sys.stderr)
        return 2
    try:
        return run(args, client, paint)
    except PrecedenzError as exc:
        status = f" ({exc.status_code})" if getattr(exc, "status_code", None) else ""
        print(err("bad", f"precedenz: {exc}{status}"), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
