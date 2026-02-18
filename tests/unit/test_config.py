from __future__ import annotations

from awe_agentcheck.config import load_settings


def test_load_settings_includes_default_gemini_command(monkeypatch):
    monkeypatch.delenv('AWE_GEMINI_COMMAND', raising=False)
    settings = load_settings()
    assert settings.gemini_command == 'gemini --yolo'


def test_load_settings_allows_gemini_command_override(monkeypatch):
    monkeypatch.setenv('AWE_GEMINI_COMMAND', 'gemini --yolo --model gemini-2.5-pro')
    settings = load_settings()
    assert settings.gemini_command == 'gemini --yolo --model gemini-2.5-pro'


def test_load_settings_defaults_codex_reasoning_to_xhigh(monkeypatch):
    monkeypatch.delenv('AWE_CODEX_COMMAND', raising=False)
    settings = load_settings()
    assert 'model_reasoning_effort=xhigh' in settings.codex_command


def test_load_settings_defaults_claude_to_opus_4_6(monkeypatch):
    monkeypatch.delenv('AWE_CLAUDE_COMMAND', raising=False)
    settings = load_settings()
    assert '--model claude-opus-4-6' in settings.claude_command


def test_load_settings_default_database_url_uses_short_connect_timeout(monkeypatch):
    monkeypatch.delenv('AWE_DATABASE_URL', raising=False)
    settings = load_settings()
    assert 'connect_timeout=2' in settings.database_url


def test_load_settings_defaults_workflow_backend_to_langgraph(monkeypatch):
    monkeypatch.delenv('AWE_WORKFLOW_BACKEND', raising=False)
    settings = load_settings()
    assert settings.workflow_backend == 'langgraph'


def test_load_settings_allows_workflow_backend_override(monkeypatch):
    monkeypatch.setenv('AWE_WORKFLOW_BACKEND', 'classic')
    settings = load_settings()
    assert settings.workflow_backend == 'classic'


def test_load_settings_invalid_workflow_backend_falls_back_to_langgraph(monkeypatch):
    monkeypatch.setenv('AWE_WORKFLOW_BACKEND', 'invalid-backend')
    settings = load_settings()
    assert settings.workflow_backend == 'langgraph'
