from __future__ import annotations

from awe_agentcheck.config import load_settings


def test_load_settings_includes_default_gemini_command(monkeypatch):
    monkeypatch.delenv('AWE_GEMINI_COMMAND', raising=False)
    settings = load_settings()
    assert 'gemini -p' in settings.gemini_command


def test_load_settings_allows_gemini_command_override(monkeypatch):
    monkeypatch.setenv('AWE_GEMINI_COMMAND', 'gemini -p --yolo --model gemini-2.5-pro')
    settings = load_settings()
    assert settings.gemini_command == 'gemini -p --yolo --model gemini-2.5-pro'
