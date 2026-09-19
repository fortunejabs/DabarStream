"""
Phase 1 feature tests: Slides, Timers, Stage display, and auth gating.
"""

import time

import pytest

import server as server_module


@pytest.fixture(autouse=True)
def open_dev_mode(monkeypatch, tmp_path):
    """Open dev mode + disposable cwd unless a test overrides the key."""
    monkeypatch.delenv(server_module.ENV_KEY_NAME, raising=False)
    monkeypatch.chdir(tmp_path)


def _capture_emits(monkeypatch):
    emitted = []
    monkeypatch.setattr(server_module, "emit", lambda e, p, **kw: emitted.append((e, p)))
    return emitted


# --- Slide payload validation -------------------------------------------------
def test_validate_slide_payload_accepts_clean_slide():
    cleaned = server_module.validate_slide_payload(
        {"title": "  Announcements  ", "lines": [" Youth camp ", "Sept 1 "],
         "theme": {"bg": "#101010", "color": "#ffffff"}}
    )
    assert cleaned == {
        "title": "Announcements",
        "lines": ["Youth camp", "Sept 1"],
        "theme": {"bg": "#101010", "color": "#ffffff"},
    }


def test_validate_slide_payload_accepts_no_theme():
    cleaned = server_module.validate_slide_payload(
        {"title": "Title", "lines": ["Only line"]}
    )
    assert cleaned == {"title": "Title", "lines": ["Only line"], "theme": {}}


def test_validate_slide_payload_rejects_malformed():
    v = server_module.validate_slide_payload
    assert v(None) is None
    assert v("hi") is None
    assert v({}) is None
    assert v({"title": "", "lines": ["x"]}) is None
    assert v({"title": "x" * 121, "lines": ["y"]}) is None
    assert v({"title": "T", "lines": "not-a-list"}) is None
    assert v({"title": "T", "lines": []}) is None
    assert v({"title": "T", "lines": [123]}) is None
    assert v({"title": "T", "lines": ["word " * 21]}) is None
    assert v({"title": "T", "lines": ["x" * 501]}) is None
    # Theme keys are whitelisted
    assert v({"title": "T", "lines": ["l"], "theme": {"evil": "x"}}) is None
    assert v({"title": "T", "lines": ["l"], "theme": {"bg": 5}}) is None


# --- Slide handler: auth + broadcast ------------------------------------------
def test_show_slide_broadcasts_when_authorized(monkeypatch):
    monkeypatch.setenv(server_module.ENV_KEY_NAME, "s3cret")
    emitted = _capture_emits(monkeypatch)
    server_module.handle_show_slide(
        {"title": "Welcome", "lines": ["Grace to you"], "key": "s3cret"}
    )
    assert emitted == [(
        "update_slide",
        {"title": "Welcome", "lines": ["Grace to you"], "theme": {}},
    )]


def test_show_slide_rejects_bad_key(monkeypatch):
    monkeypatch.setenv(server_module.ENV_KEY_NAME, "s3cret")
    emitted = _capture_emits(monkeypatch)
    server_module.handle_show_slide({"title": "Welcome", "lines": ["x"], "key": "wrong"})
    server_module.handle_show_slide({"title": "Welcome", "lines": ["x"]})
    assert emitted == []


def test_show_slide_ignores_malformed(monkeypatch):
    emitted = _capture_emits(monkeypatch)
    server_module.handle_show_slide({"title": "No lines here"})
    assert emitted == []


# --- Timer validation + handler -----------------------------------------------
def test_validate_timer_payload_accepts_start_stop_clear():
    started = server_module.validate_timer_payload(
        {"action": "start", "minutes": "12.5", "label": " Sermon "}
    )
    assert started["action"] == "start"
    assert started["label"] == "Sermon"
    assert started["ends_at"] > time.time()

    stopped = server_module.validate_timer_payload({"action": "stop", "label": "Sermon"})
    assert stopped["action"] == "stop" and stopped["ends_at"] is None

    cleared = server_module.validate_timer_payload({"action": "clear"})
    assert cleared == {"action": "clear", "label": "", "ends_at": None}


def test_validate_timer_payload_rejects_malformed():
    v = server_module.validate_timer_payload
    assert v(None) is None
    assert v({}) is None
    assert v({"action": "pause"}) is None
    assert v({"action": "start"}) is None
    assert v({"action": "start", "minutes": 0}) is None
    assert v({"action": "start", "minutes": 2000}) is None
    assert v({"action": "start", "minutes": "half"}) is None
    assert v({"action": "stop", "label": 9}) is None


def test_timer_control_broadcasts_when_authorized(monkeypatch):
    monkeypatch.setenv(server_module.ENV_KEY_NAME, "s3cret")
    emitted = _capture_emits(monkeypatch)
    server_module.handle_timer_control(
        {"action": "start", "minutes": 30, "label": "Sermon", "key": "s3cret"}
    )
    assert len(emitted) == 1
    event, payload = emitted[0]
    assert event == "timer_update"
    assert payload["action"] == "start"
    assert payload["label"] == "Sermon"
    assert payload["ends_at"] > time.time()


def test_timer_control_rejects_bad_key(monkeypatch):
    monkeypatch.setenv(server_module.ENV_KEY_NAME, "s3cret")
    emitted = _capture_emits(monkeypatch)
    server_module.handle_timer_control({"action": "start", "minutes": 5, "key": "nope"})
    assert emitted == []


# --- Routes --------------------------------------------------------------------
def test_stage_route_serves_page():
    client = server_module.app.test_client()
    response = client.get("/stage")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    for marker in ("Stage Display", "update_overlay", "update_slide",
                   "timer_update", "clear_overlay"):
        assert marker in body


def test_control_route_still_serves():
    client = server_module.app.test_client()
    assert client.get("/control").status_code == 200


# --- Deployment health probe ---------------------------------------------------
def test_health_reports_missing_database():
    """With no bible.db beside server.py, /health must report it, not 500."""
    payload = server_module.app.test_client().get("/health").get_json()
    assert payload["status"] == "ok"          # liveness is unaffected
    assert payload["db_exists"] is False
    assert "db_error" in payload


def test_health_reports_verse_and_translation_counts(tmp_path, monkeypatch):
    """A deployed database must surface counts, so one curl verifies a deploy."""
    import json

    from importer import import_freeshow_json

    db = tmp_path / "bible.db"
    monkeypatch.setattr(server_module, "DB_PATH", str(db))
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps({"John": {"3": {"16": "For God so loved..."}}}),
                    encoding="utf-8")
    import_freeshow_json(str(seed), translation_code="eng", db_path=str(db))
    import_freeshow_json(str(seed), translation_code="bem", db_path=str(db))

    payload = server_module.app.test_client().get("/health").get_json()
    assert payload["status"] == "ok"
    assert payload["db_exists"] is True
    assert payload["verses"] == 2
    assert payload["translations"] == 2
