"""The map's snapshot: everything the live map draws at rest, in one read.

`snapshot()` is what `GET /map/data` returns (docs/decide-once-plan.md, 5.7). It
reads; it never decides, writes, or calls a model. Every source is optional:
an empty table, a builder's module that is not merged yet, or a missing Mubit
console snapshot each degrade to an empty list or a null, never to an error,
because a map that fails to load hides every decision that did happen.

Sources, in order of authority:

* the workstream list from `app.workstreams.service`, falling back to the
  built-in domain registry plus the stored `workstreams` table;
* `policies`: every version that was ever activated is a precedent;
* `harness_decisions`: follows hang under their precedent, unanswered and
  unresolved asks are "waiting";
* `exceptions`: cases decided in the existing inbox, marked `"via": "inbox"`;
* `harness_answers`, model-call counts, and the Mubit mirror (`app.mubit`).
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.harness import events

log = logging.getLogger(__name__)

# The person the local demo acts as; also the center of the map when nobody has
# decided anything yet.
OPERATOR = "dana.r@wharf-canary-optics.example"

FOLLOW = "follow"
ASK = "ask"

# A precedent is something a person activated. Candidates and rejected drafts
# never were, so they are not on the map.
_PRECEDENT_STATUSES = ("active", "retired", "superseded")

# Built-in workstreams, when the workstream service cannot say. Their own spec
# supplies everything else.
_BUILTIN_DEFAULTS: dict[str, dict[str, Any]] = {
    "returns.missing_accessory": {
        "label": "Returns · missing accessory",
        "question": "Can this return be closed by replacing the missing part?",
        "repeatable_answer": "replace the missing part",
        "reader": True,
    },
    "ap.invoice_variance": {
        "label": "AP invoices · price variance",
        "question": "Can this invoice variance be approved?",
        "repeatable_answer": "approve the variance",
        "reader": False,
    },
}

_TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "workstreams"


# ---------------------------------------------------------------- helpers


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _loads(raw: Any, default: Any) -> Any:
    if raw is None or raw == "":
        return default
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _plain(item: Any) -> dict[str, Any]:
    """A dict from whatever a service returns: dict, pydantic model, or dataclass."""
    if isinstance(item, dict):
        return dict(item)
    if hasattr(item, "model_dump"):
        return item.model_dump()
    if is_dataclass(item) and not isinstance(item, type):
        return asdict(item)
    return dict(getattr(item, "__dict__", {}))


def person_label(person: str | None) -> str:
    """"dana.r@wharf-canary-optics.example" reads as "Dana R."; anything else as given."""
    if not person:
        return ""
    local, at, _ = person.partition("@")
    if not at:
        return person
    parts = [p for p in re.split(r"[._+\-]+", local) if p]
    if not parts:
        return person
    first = parts[0].capitalize()
    if len(parts) == 1:
        return first
    return f"{first} {parts[1][0].upper()}."


def _optional(module: str, name: str):
    """A function another builder owns, or None while it is not merged."""
    try:
        mod = __import__(module, fromlist=[name])
        return getattr(mod, name)
    except (ImportError, AttributeError):
        return None
    except Exception:  # a module that fails on import is as absent as a missing one
        log.warning("map snapshot: %s failed to import", module, exc_info=True)
        return None


def _safe_call(fn, *args, **kwargs):
    if fn is None:
        return None
    try:
        return fn(*args, **kwargs)
    except Exception:  # a mirror or a list must never take the map down
        log.warning("map snapshot: %s failed", getattr(fn, "__name__", fn), exc_info=True)
        return None


# ------------------------------------------------------------ workstreams


def _builtin_entry(spec: Any) -> dict[str, Any]:
    defaults = _BUILTIN_DEFAULTS.get(spec.family, {})
    fields: list[dict[str, Any]] = []
    for name in sorted(spec.bool_fields):
        fields.append({"name": name, "kind": "flag", "label": name.replace("_", " "), "ceiling": None})
    for name, values in sorted(spec.enum_fields.items()):
        fields.append({"name": name, "kind": "category", "label": name.replace("_", " "),
                       "ceiling": None, "values": sorted(values)})
    for name in sorted(spec.number_fields):
        fields.append({"name": name, "kind": "amount", "label": name.replace("_", " "),
                       "ceiling": spec.numeric_ceilings.get(name)})
    return {
        "id": spec.family,
        "label": defaults.get("label", spec.label),
        "kind": "built-in",
        "audience": "team",
        "subject": spec.subject,
        "question": defaults.get("question", f"What should happen with this {spec.subject}?"),
        "repeatable_answer": getattr(spec, "repeatable_answer", None)
        or defaults.get("repeatable_answer", ""),
        "other_answers": list(getattr(spec, "other_answers", None) or []),
        "fields": fields,
        "reader": defaults.get("reader", False),
        "evidence": "text" if spec.evidence_text else "none",
    }


def _contract_entry(contract: dict[str, Any], *, kind: str = "planted") -> dict[str, Any]:
    return {
        "id": contract.get("id", ""),
        "label": contract.get("label") or contract.get("id", ""),
        "kind": kind,
        "audience": contract.get("audience", "team"),
        "subject": contract.get("subject", ""),
        "question": contract.get("question", ""),
        "repeatable_answer": contract.get("repeatable_answer", ""),
        "other_answers": list(contract.get("other_answers") or []),
        "fields": [
            {
                "name": f.get("name"),
                "kind": f.get("kind"),
                "label": f.get("label") or f.get("name"),
                "ceiling": f.get("ceiling"),
                **({"values": f["values"]} if f.get("values") else {}),
            }
            for f in contract.get("fields") or []
            if isinstance(f, dict)
        ],
        "reader": bool(contract.get("reader", False)),
        "evidence": contract.get("evidence", "none"),
    }


def _fallback_workstreams(conn) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        from app import domains

        domains.load()
        planted_ids = {r["id"] for r in conn.execute("SELECT id FROM workstreams").fetchall()}
        for spec in domains.all_specs():
            if spec.family not in planted_ids:
                out.append(_builtin_entry(spec))
    except Exception:
        log.warning("map snapshot: built-in workstreams unavailable", exc_info=True)
    try:
        for row in conn.execute("SELECT * FROM workstreams ORDER BY planted_at").fetchall():
            contract = _loads(row["contract"], {})
            contract.setdefault("id", row["id"])
            contract.setdefault("audience", row["audience"])
            entry = _contract_entry(contract)
            entry["planted_by"] = row["planted_by"]
            entry["planted_at"] = row["planted_at"]
            out.append(entry)
    except Exception:
        log.warning("map snapshot: planted workstreams unavailable", exc_info=True)
    return out


def _workstream_list(conn) -> list[dict[str, Any]]:
    listed = _safe_call(_optional("app.workstreams.service", "list_workstreams"))
    if not listed:
        return _fallback_workstreams(conn)
    out = []
    for item in listed:
        raw = _plain(item)
        entry = _contract_entry(raw, kind=raw.get("kind") or "planted")
        for extra in ("planted_by", "planted_at", "owner"):
            if raw.get(extra) is not None:
                entry[extra] = raw[extra]
        out.append(entry)
    # The built-ins are always on the map, even from a list of planted ones only.
    listed_ids = {entry["id"] for entry in out}
    missing = [family for family in _BUILTIN_DEFAULTS if family not in listed_ids]
    if missing:
        try:
            from app import domains

            domains.load()
            out[:0] = [_builtin_entry(domains.get(family)) for family in missing
                       if family in domains.families()]
        except Exception:
            log.warning("map snapshot: built-in workstreams unavailable", exc_info=True)
    return out


def _templates(planted: set[str]) -> list[dict[str, Any]]:
    listed = _safe_call(_optional("app.workstreams.service", "list_templates"))
    items: list[dict[str, Any]] = []
    if listed:
        for item in listed:
            raw = _plain(item)
            contract = raw.get("contract") if isinstance(raw.get("contract"), dict) else raw
            items.append({"id": raw.get("id") or contract.get("id"),
                          "label": contract.get("label") or raw.get("label"),
                          "audience": contract.get("audience") or raw.get("audience", "team")})
    elif _TEMPLATE_DIR.is_dir():
        for path in sorted(_TEMPLATE_DIR.glob("*.json")):
            try:
                contract = json.loads(path.read_text()).get("contract", {})
            except (OSError, ValueError):
                continue
            items.append({"id": contract.get("id", path.stem), "label": contract.get("label", path.stem),
                          "audience": contract.get("audience", "team")})
    for item in items:
        item["planted"] = item["id"] in planted
    return items


# ------------------------------------------------------------- precedents


def _condition_text(c: dict[str, Any]) -> str:
    ops = {"eq": "=", "ne": "≠", "lt": "<", "lte": "≤", "gt": ">", "gte": "≥", "in": "in"}
    value = c.get("value")
    if isinstance(value, bool):
        value = "yes" if value else "no"
    return f"{c.get('field')} {ops.get(c.get('operator'), c.get('operator'))} {value}"


def _precedents(conn) -> dict[str, list[dict[str, Any]]]:
    marks = ",".join("?" for _ in _PRECEDENT_STATUSES)
    rows = conn.execute(
        f"""SELECT policy_id, family, version, status, definition, origin_case_id,
                   activated_at, activated_by, retired_at, created_at
              FROM policies
             WHERE status IN ({marks}) AND (activated_at IS NOT NULL OR status = ?)
             ORDER BY family, version""",
        (*_PRECEDENT_STATUSES, "active"),
    ).fetchall()
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        definition = _loads(row["definition"], {})
        by_family[row["family"]].append({
            "id": row["policy_id"],
            "version": row["version"],
            "status": row["status"],
            "name": definition.get("name", ""),
            "text": definition.get("description") or definition.get("name", ""),
            "conditions": [_condition_text(c) for c in definition.get("conditions") or []
                           if isinstance(c, dict)],
            "actions": [a for a in definition.get("actions") or [] if isinstance(a, dict)],
            "decided_by": row["activated_by"],
            "decided_at": row["activated_at"],
            "retired_at": row["retired_at"],
            "origin_case": row["origin_case_id"],
            "cases": [],
        })
    return by_family


# --------------------------------------------------------------- snapshot


def _empty_counts() -> dict[str, int]:
    return {FOLLOW: 0, ASK: 0, "answered": 0, "model_calls": 0}


def snapshot() -> dict[str, Any]:
    """The whole map at rest. Never raises: a broken source becomes an empty one."""
    from app import db

    out: dict[str, Any] = {
        "generated_at": _now(),
        "last_seq": events.last_seq(),
        "people": [],
        "workstreams": [],
        "templates": [],
        "totals": {"answered_once": 0, "repeated": 0, "asked": 0, "model_calls": 0},
    }
    try:
        with db.read_only() as conn:
            _fill(conn, out)
    except Exception:
        log.exception("map snapshot failed; serving what was gathered")
    if not out["people"]:
        out["people"] = [{"id": OPERATOR, "label": person_label(OPERATOR)}]
    return out


def _fill(conn, out: dict[str, Any]) -> None:
    workstreams = {ws["id"]: ws for ws in _workstream_list(conn) if ws.get("id")}
    precedents = _guard(lambda: _precedents(conn), {})
    decisions = _guard(lambda: _rows(conn, "SELECT * FROM harness_decisions ORDER BY created_at"), [])
    answers = _guard(lambda: _rows(conn, "SELECT * FROM harness_answers ORDER BY created_at"), [])
    cases = _guard(lambda: _case_inputs(conn), {})
    inbox = _guard(lambda: _rows(conn, "SELECT * FROM exceptions ORDER BY updated_at"), [])
    inbox_answers = _guard(lambda: _inbox_answer_counts(conn), {})

    # Anything decided under a family the list does not know still happened.
    seen = set(precedents) | {d["workstream"] for d in decisions}
    for family in sorted(seen - set(workstreams)):
        workstreams[family] = _contract_entry({"id": family, "label": family}, kind="built-in")

    decision_ws = {d["decision_id"]: d["workstream"] for d in decisions}
    answered_ids = {a["decision_id"] for a in answers}
    resolved_ids = {d["resolves"] for d in decisions if d.get("resolves")}
    latest_for_case: dict[tuple[str, str], str] = {}
    for d in decisions:
        latest_for_case[(d["workstream"], d["case_ref"])] = d["decision_id"]

    for ws_id, ws in workstreams.items():
        ws["precedents"] = precedents.get(ws_id, [])
        ws["waiting"] = []
        ws["answered"] = []
        ws["counts"] = _empty_counts()
        stage = _safe_call(_optional("app.mubit", "console_stage"), ws_id)
        ws["mubit"] = _plain(stage) if stage else None
        ws.setdefault("task_key", None)
        ws.setdefault("task_key_changed", False)
    by_precedent = {p["id"]: p for plist in precedents.values() for p in plist}
    task_keys: dict[str, list[str]] = defaultdict(list)

    for d in decisions:
        ws = workstreams[d["workstream"]]
        calls = _loads(d.get("model_calls"), [])
        ws["counts"]["model_calls"] += len(calls) if isinstance(calls, list) else 0
        if d.get("task_key") and d["task_key"] not in task_keys[d["workstream"]]:
            task_keys[d["workstream"]].append(d["task_key"])
        if d["verdict"] == FOLLOW:
            ws["counts"][FOLLOW] += 1
            precedent = by_precedent.get(d.get("precedent_id") or "")
            if precedent is not None:
                precedent["cases"].append({
                    "case_ref": d["case_ref"], "decision_id": d["decision_id"], "verdict": FOLLOW,
                    "at": d["created_at"], "via": d.get("caller") or "api", "resolves": d.get("resolves"),
                })
            continue
        ws["counts"][ASK] += 1
        waiting = (
            d["decision_id"] not in answered_ids
            and d["decision_id"] not in resolved_ids
            and latest_for_case.get((d["workstream"], d["case_ref"])) == d["decision_id"]
        )
        if waiting:
            case = cases.get((d["workstream"], d["case_ref"]), {})
            ws["waiting"].append({
                "case_ref": d["case_ref"], "decision_id": d["decision_id"],
                "reasons": _loads(d.get("reasons"), []), "at": d["created_at"],
                "via": d.get("caller") or "api", "nearest_precedent": d.get("precedent_id"),
                "facts": case.get("facts", {}), "text": case.get("text"), "answerable": True,
            })

    for a in answers:
        ws_id = decision_ws.get(a["decision_id"])
        if ws_id is None:
            continue
        workstreams[ws_id]["counts"]["answered"] += 1
        workstreams[ws_id]["answered"].append({
            "decision_id": a["decision_id"], "answer": a["answer"], "remembered": bool(a["remembered"]),
            "precedent_id": a.get("precedent_id"), "by": a["actor"], "at": a["created_at"],
        })

    _add_inbox(workstreams, by_precedent, inbox, inbox_answers)

    for ws_id, ws in workstreams.items():
        info = _safe_call(_optional("app.mubit", "task_key_info"), ws_id)
        info = _plain(info) if info else None
        if info:
            ws["task_key"] = info.get("value")
            ws["task_key_changed"] = bool(info.get("changed"))
            ws["task_key_since"] = info.get("since")
            ws["task_key_previous"] = info.get("previous")
        elif task_keys.get(ws_id):
            # The mirror only knows keys built since the server started; the
            # receipts remember every key a decision was made under.
            keys = task_keys[ws_id]
            ws["task_key"] = keys[-1]
            ws["task_key_changed"] = len(keys) > 1
            ws["task_key_previous"] = keys[-2] if len(keys) > 1 else None

    out["workstreams"] = list(workstreams.values())
    out["templates"] = _guard(lambda: _templates(set(workstreams)), [])
    out["people"] = _people(precedents, answers)
    totals = out["totals"]
    for ws in out["workstreams"]:
        totals["answered_once"] += len(ws["precedents"])
        totals["repeated"] += ws["counts"][FOLLOW]
        totals["asked"] += ws["counts"][ASK]
        totals["model_calls"] += ws["counts"]["model_calls"]


def _add_inbox(workstreams, by_precedent, inbox, inbox_answers) -> None:
    """Cases decided in the existing inbox: resolved is a follow, escalated an ask."""
    for row in inbox:
        family = row.get("family") or "returns.missing_accessory"
        ws = workstreams.get(family)
        if ws is None:
            continue
        status = row["status"]
        if status == "resolved":
            ws["counts"][FOLLOW] += 1
            precedent = by_precedent.get(row.get("applied_policy_id") or "")
            if precedent is not None:
                precedent["cases"].append({
                    "case_ref": row["case_id"], "decision_id": row["exception_id"], "verdict": FOLLOW,
                    "at": row["updated_at"], "via": "inbox", "resolves": None,
                })
        elif status in ("escalated", "waiting_decision"):
            ws["counts"][ASK] += 1
            ws["waiting"].append({
                "case_ref": row["case_id"], "decision_id": row["exception_id"],
                "reasons": _loads(row.get("escalation_reasons"), []) or [row.get("summary") or status],
                "at": row["updated_at"], "via": "inbox", "nearest_precedent": None,
                "facts": {}, "text": None, "answerable": False,
            })
    for family, count in inbox_answers.items():
        if family in workstreams:
            workstreams[family]["counts"]["answered"] += count


def _people(precedents, answers) -> list[dict[str, str]]:
    ids: list[str] = []
    for plist in precedents.values():
        for p in plist:
            if p["decided_by"] and p["decided_by"] not in ids:
                ids.append(p["decided_by"])
    for a in answers:
        if a.get("actor") and "@" in a["actor"] and a["actor"] not in ids:
            ids.append(a["actor"])
    if OPERATOR in ids:  # the operator is the center of the map
        ids.remove(OPERATOR)
        ids.insert(0, OPERATOR)
    return [{"id": i, "label": person_label(i)} for i in ids]


def _rows(conn, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _case_inputs(conn) -> dict[tuple[str, str], dict[str, Any]]:
    out = {}
    for r in conn.execute("SELECT workstream, case_ref, facts, text FROM harness_cases").fetchall():
        out[(r["workstream"], r["case_ref"])] = {"facts": _loads(r["facts"], {}), "text": r["text"]}
    return out


def _inbox_answer_counts(conn) -> dict[str, int]:
    rows = conn.execute(
        """SELECT e.family AS family, COUNT(*) AS n
             FROM decision_cards d JOIN exceptions e ON e.exception_id = d.exception_id
            WHERE d.outcome IS NOT NULL
            GROUP BY e.family"""
    ).fetchall()
    return {r["family"]: int(r["n"]) for r in rows}


def _guard(fn, default):
    try:
        return fn()
    except Exception:
        log.warning("map snapshot: a source failed", exc_info=True)
        return default


# ---------------------------------------------------------------- receipts


def receipt(decision_id: str) -> dict[str, Any] | None:
    """One receipt for the map drawer: the decision service's, or read from the tables.

    An inbox case's id (an exception id) gets a short receipt pointing at the
    inbox page, since the inbox, not the harness, decided it.
    """
    get_receipt = _optional("app.harness.service", "get_receipt")
    if get_receipt is not None:
        try:
            found = get_receipt(decision_id)
        except LookupError:
            found = None
        if found is not None:
            return _plain(found)
    from app import db

    with db.read_only() as conn:
        row = conn.execute(
            "SELECT * FROM harness_decisions WHERE decision_id = ?", (decision_id,)
        ).fetchone()
        if row is not None:
            return _receipt_from_row(conn, dict(row))
        exc = conn.execute(
            "SELECT * FROM exceptions WHERE exception_id = ?", (decision_id,)
        ).fetchone()
        return _inbox_receipt(dict(exc)) if exc is not None else None


def _receipt_from_row(conn, d: dict[str, Any]) -> dict[str, Any]:
    for key, default in (("actions", []), ("reasons", []), ("checks", []), ("model_calls", [])):
        d[key] = _loads(d.get(key), default)
    precedent = None
    if d.get("precedent_id"):
        prow = conn.execute(
            "SELECT * FROM policies WHERE policy_id = ?", (d["precedent_id"],)
        ).fetchone()
        if prow is not None:
            definition = _loads(prow["definition"], {})
            precedent = {
                "id": prow["policy_id"], "version": prow["version"], "status": prow["status"],
                "text": definition.get("description") or definition.get("name", ""),
                "decided_by": prow["activated_by"], "decided_at": prow["activated_at"],
                "origin_case": prow["origin_case_id"],
            }
    d["precedent"] = precedent
    d["answers"] = _rows(conn, "SELECT * FROM harness_answers WHERE decision_id = ? ORDER BY created_at",
                         (d["decision_id"],))
    d["outcomes"] = _rows(conn, "SELECT * FROM harness_outcomes WHERE decision_id = ? ORDER BY created_at",
                          (d["decision_id"],))
    resolver = conn.execute(
        "SELECT decision_id FROM harness_decisions WHERE resolves = ? ORDER BY created_at LIMIT 1",
        (d["decision_id"],),
    ).fetchone()
    d["resolved_by"] = resolver["decision_id"] if resolver else None
    d.setdefault("serving_identity", "not reported by the gateway")
    return d


def _inbox_receipt(row: dict[str, Any]) -> dict[str, Any]:
    status = row["status"]
    verdict = FOLLOW if status == "resolved" else ASK if status in ("escalated", "waiting_decision") else status
    return {
        "decision_id": row["exception_id"],
        "verdict": verdict,
        "workstream": row.get("family") or "returns.missing_accessory",
        "case_ref": row["case_id"],
        "via": "inbox",
        "status": status,
        "precedent": {"id": row["applied_policy_id"]} if row.get("applied_policy_id") else None,
        "reasons": _loads(row.get("escalation_reasons"), []),
        "summary": row.get("summary", ""),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "link": f"/exceptions/{row['exception_id']}",
    }
