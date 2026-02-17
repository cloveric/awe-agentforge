from __future__ import annotations

from fastapi.testclient import TestClient

from awe_agentcheck.main import build_app


def test_build_app_falls_back_to_in_memory_repo_on_bad_database_url(monkeypatch):
    monkeypatch.setenv('AWE_DATABASE_URL', 'invalid+driver://bad')
    app = build_app()
    client = TestClient(app)

    resp = client.get('/healthz')
    assert resp.status_code == 200
    assert resp.json()['status'] == 'ok'


def test_build_app_wires_gemini_command_into_runner(monkeypatch):
    captured: dict[str, object] = {}

    class FakeRunner:
        def __init__(self, *, command_overrides, dry_run, timeout_retries):
            captured['command_overrides'] = dict(command_overrides)
            captured['dry_run'] = dry_run
            captured['timeout_retries'] = timeout_retries

    monkeypatch.setenv('AWE_DATABASE_URL', 'invalid+driver://bad')
    monkeypatch.setenv('AWE_GEMINI_COMMAND', 'gemini -p --yolo --model gemini-2.5-pro')
    monkeypatch.setattr('awe_agentcheck.main.ParticipantRunner', FakeRunner)

    app = build_app()
    client = TestClient(app)
    resp = client.get('/healthz')

    assert resp.status_code == 200
    overrides = captured.get('command_overrides')
    assert isinstance(overrides, dict)
    assert overrides.get('gemini') == 'gemini -p --yolo --model gemini-2.5-pro'
