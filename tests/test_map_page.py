"""The map page: it renders on its own, loads its assets, and its fixture keeps
the documented snapshot shape (docs/decide-once-plan.md, 5.7 and section 6)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
FIXTURE = STATIC / "pages" / "map.fixture.json"

# Only the stage words the Mubit console uses (plan, brief B5); the map infers nothing.
MUBIT_STAGES = {
    "Collecting examples", "Training", "Passed", "Below target", "Prompt only",
    "Not tested", "Silent test", "Partial rollout", "Live",
}


@pytest.fixture()
def client(temp_db):
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def test_map_renders_and_references_its_assets(client):
    r = client.get("/map")
    assert r.status_code == 200
    html = r.text
    # Versioned, so a browser picks up a new copy after an update.
    assert "/static/pages/map.js?v=" in html
    assert "/static/pages/map.css?v=" in html
    assert "/static/vendor/motion-11.18.2.min.js" in html
    # Dark only and full bleed: the page does not pull in the app's light design system.
    assert "/static/app.css" not in html
    assert 'id="map-svg"' in html and 'id="map-fx"' in html and 'id="map-plankton"' in html


def test_map_page_carries_the_hud_and_accessibility_hooks(client):
    html = client.get("/map").text
    assert "Never answer the same question twice." in html
    assert "Every pulse is a real decision." in html
    assert "Synthetic demo data" in html
    for label in ("Answered once", "Repeated", "Asked", "Model calls via Mubit"):
        assert label in html
    assert 'aria-live="polite"' in html
    assert 'id="map-table"' in html  # the screen-reader table


def test_map_page_embeds_its_config_as_json(client):
    html = client.get("/map").text
    start = html.index('id="map-config">') + len('id="map-config">')
    config = json.loads(html[start:html.index("</script>", start)])
    assert {"asset_version", "company", "provider", "operator", "demo_approval_token"} <= config.keys()


def test_map_assets_are_served(client):
    for path in (
        "/static/pages/map.js",
        "/static/pages/map.css",
        "/static/pages/map.fixture.json",
        "/static/vendor/motion-11.18.2.min.js",
    ):
        assert client.get(path).status_code == 200, path


def test_map_css_declares_the_section_6_tokens_and_fonts():
    css = (STATIC / "pages" / "map.css").read_text()
    for token in ("--abyss: #02050b", "--ask: #ffb547", "--decide: #f4fbff", "--overrule: #ff5a6e",
                  "--dormant: #2a3550", "--mubit: #7c7bff", "--hue-1: #42e8ff", "--hue-5: #c4ff6b"):
        assert token in css
    assert '"Geist Sans"' in css and '"Geist Mono"' in css
    assert "/static/fonts/geist-sans-latin-400-normal.woff2" in css
    assert "prefers-reduced-motion" in css


def test_map_js_exposes_the_recording_api():
    js = (STATIC / "pages" / "map.js").read_text()
    assert "window.precedenzMap" in js
    for name in ("focus:", "reset:", "openReceipt:", "openWaiting:", "closeDrawer:"):
        assert name in js
    for route in ("/map/data", "/map/events", "/map/receipt/", "/map/answer/", "/map/overrule/"):
        assert route in js
    assert 'globalCompositeOperation = "lighter"' in js
    assert "getPointAtLength" in js


def test_fixture_has_the_documented_keys(fixture):
    assert {"generated_at", "last_seq", "people", "workstreams", "templates", "totals"} <= fixture.keys()
    assert fixture["people"] and {"id", "label"} <= fixture["people"][0].keys()
    assert {"answered_once", "repeated", "asked", "model_calls"} == set(fixture["totals"])
    for t in fixture["templates"]:
        assert {"id", "label", "audience"} <= t.keys()

    for ws in fixture["workstreams"]:
        assert {
            "id", "label", "kind", "audience", "reader", "task_key", "task_key_changed", "mubit",
            "precedents", "waiting", "counts", "repeatable_answer", "other_answers", "fields",
        } <= ws.keys(), ws["id"]
        assert ws["audience"] in {"team", "individual"}
        assert {"follow", "ask", "answered", "model_calls"} == set(ws["counts"])
        if ws["mubit"] is not None:
            assert {"stage", "detail", "source", "as_of"} <= ws["mubit"].keys()
            assert ws["mubit"]["stage"] in MUBIT_STAGES
        for field in ws["fields"]:
            assert {"name", "kind", "label", "ceiling"} <= field.keys()
            assert field["kind"] in {"flag", "category", "amount", "count"}
        for p in ws["precedents"]:
            assert {"id", "version", "status", "text", "decided_by", "decided_at", "origin_case", "cases"} <= p.keys()
            for case in p["cases"]:
                assert {"case_ref", "decision_id", "verdict", "at", "via"} <= case.keys()
        for w in ws["waiting"]:
            assert {"case_ref", "decision_id", "reasons", "at", "facts", "text"} <= w.keys()


def test_fixture_covers_every_state_the_map_draws(fixture):
    by_id = {ws["id"]: ws for ws in fixture["workstreams"]}
    statuses = {p["status"] for ws in by_id.values() for p in ws["precedents"]}
    assert {"active", "retired"} <= statuses
    returns = by_id["returns.missing_accessory"]
    assert returns["kind"] == "built-in" and returns["mubit"]["stage"] == "Collecting examples"
    assert len(returns["waiting"]) == 3
    ap = by_id["ap.invoice_variance"]
    assert ap["reader"] is False and ap["mubit"] is None  # rules only: no arc
    meals = by_id["expenses.meal_claim"]
    assert meals["kind"] == "planted" and len(meals["waiting"]) == 2
    assert any(len(p["cases"]) == 12 and p["status"] == "active" for p in meals["precedents"])
    assert by_id["calendar.meeting_invite"]["audience"] == "individual"
    assert any(ws["reader"] and ws["mubit"] is None for ws in by_id.values())  # a reader without a snapshot


def test_fixture_totals_add_up(fixture):
    ws = fixture["workstreams"]
    totals = fixture["totals"]
    assert totals["repeated"] == sum(w["counts"]["follow"] for w in ws)
    assert totals["asked"] == sum(w["counts"]["ask"] for w in ws)
    assert totals["model_calls"] == sum(w["counts"]["model_calls"] for w in ws)
    assert totals["answered_once"] == sum(len(w["precedents"]) for w in ws)
    for w in ws:
        followed = sum(len(p["cases"]) for p in w["precedents"])
        assert w["counts"]["follow"] == followed, w["id"]
