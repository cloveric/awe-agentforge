from __future__ import annotations

from pathlib import Path
import re


def test_dialogue_panel_uses_defined_avatar_renderer_reference():
    dashboard_path = Path(__file__).resolve().parents[2] / 'web' / 'assets' / 'dashboard.js'
    text = dashboard_path.read_text(encoding='utf-8')
    assert 'avatarHtml: avatarRenderer.avatarHtml' in text
    assert re.search(r'^\s*avatarHtml,\s*$', text, flags=re.MULTILINE) is None
