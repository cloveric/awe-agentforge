from __future__ import annotations

from pathlib import Path


def test_dockerfile_installs_runtime_dependencies_only():
    dockerfile_path = Path(__file__).resolve().parents[2] / 'Dockerfile'
    text = dockerfile_path.read_text(encoding='utf-8')
    assert 'pip install -e .[dev]' not in text
    assert 'pip install -e .' in text
