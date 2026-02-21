from __future__ import annotations

from pathlib import Path


from awe_agentcheck.workflow_architecture import (
    architecture_audit_mode,
    architecture_thresholds_for_level,
    build_environment_context,
    run_architecture_audit,
    workspace_tree_excerpt,
)


def test_workspace_tree_excerpt_handles_oserror_and_relative_to_value_error(tmp_path: Path, monkeypatch):
    root = tmp_path / 'root'
    root.mkdir(parents=True, exist_ok=True)
    (root / 'a.txt').write_text('x', encoding='utf-8')
    (root / 'dir').mkdir(parents=True, exist_ok=True)
    (root / 'dir' / 'b.txt').write_text('x', encoding='utf-8')

    original_iterdir = Path.iterdir
    monkeypatch.setattr(Path, 'iterdir', lambda self: (_ for _ in ()).throw(OSError('boom')))
    unavailable = workspace_tree_excerpt(root, max_depth=2, max_entries=5)
    assert unavailable == '- [n/a] workspace tree unavailable'
    monkeypatch.setattr(Path, 'iterdir', original_iterdir)

    original_relative_to = Path.relative_to

    def fake_relative_to(self, *args, **kwargs):  # noqa: ANN001
        if self.name == 'b.txt':
            raise ValueError('forced')
        return original_relative_to(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'relative_to', fake_relative_to)
    text = workspace_tree_excerpt(root, max_depth=3, max_entries=10)
    assert '[F] b.txt' in text


def test_architecture_thresholds_and_mode_env_overrides(monkeypatch):
    t3 = architecture_thresholds_for_level(3)
    assert t3['python_file_lines_max'] == 800

    monkeypatch.setenv('AWE_ARCH_PYTHON_FILE_LINES_MAX', 'bad')
    monkeypatch.setenv('AWE_ARCH_FRONTEND_FILE_LINES_MAX', '5')
    t2 = architecture_thresholds_for_level(2)
    assert t2['python_file_lines_max'] == 1000
    assert t2['frontend_file_lines_max'] == 10  # minimum clamp

    monkeypatch.setenv('AWE_ARCH_AUDIT_MODE', 'off')
    assert architecture_audit_mode(3) == 'off'
    monkeypatch.setenv('AWE_ARCH_AUDIT_MODE', 'hard')
    assert architecture_audit_mode(1) == 'hard'
    monkeypatch.setenv('AWE_ARCH_AUDIT_MODE', 'invalid')
    assert architecture_audit_mode(1) == 'warn'
    assert architecture_audit_mode(2) == 'warn'
    assert architecture_audit_mode(3) == 'hard'


def test_run_architecture_audit_workspace_missing_and_level_off(tmp_path: Path):
    off = run_architecture_audit(cwd=tmp_path, evolution_level=0)
    assert off.enabled is False
    assert off.reason == 'skipped'

    missing = run_architecture_audit(cwd=tmp_path / 'missing', evolution_level=1)
    assert missing.enabled is True
    assert missing.passed is False
    assert missing.reason == 'architecture_audit_workspace_missing'


def test_run_architecture_audit_detects_multiple_violation_types(tmp_path: Path, monkeypatch):
    root = tmp_path / 'repo'
    (root / 'src' / 'awe_agentcheck').mkdir(parents=True, exist_ok=True)
    (root / 'web' / 'assets').mkdir(parents=True, exist_ok=True)
    (root / 'scripts').mkdir(parents=True, exist_ok=True)

    # Large mixed-responsibility python file.
    mixed_text = '\n'.join(
        [
            'sandbox policy analytics evolution proposal merge history review workflow database api session theme avatar benchmark preflight runtime'
            for _ in range(320)
        ]
    )
    (root / 'src' / 'awe_agentcheck' / 'mixed.py').write_text(mixed_text, encoding='utf-8')
    (root / 'src' / 'awe_agentcheck' / 'service.py').write_text('\n'.join(['x=1'] * 80), encoding='utf-8')
    (root / 'src' / 'awe_agentcheck' / 'workflow.py').write_text(('_prompt(\n' * 50) + 'x=1\n', encoding='utf-8')
    (root / 'src' / 'awe_agentcheck' / 'adapters.py').write_text('raise RuntimeError("x")\n', encoding='utf-8')
    (root / 'web' / 'assets' / 'dashboard.js').write_text('\n'.join(['let x=1;'] * 80), encoding='utf-8')
    (root / 'scripts' / 'start_api.ps1').write_text('Write-Host "start"', encoding='utf-8')
    (root / 'trigger.py').write_text('print("x")\n', encoding='utf-8')

    # Force tiny thresholds to trigger all relevant violations quickly.
    monkeypatch.setenv('AWE_ARCH_AUDIT_MODE', 'hard')
    monkeypatch.setenv('AWE_ARCH_PYTHON_FILE_LINES_MAX', '20')
    monkeypatch.setenv('AWE_ARCH_RESPONSIBILITY_KEYWORDS_MAX', '2')
    monkeypatch.setenv('AWE_ARCH_SERVICE_FILE_LINES_MAX', '20')
    monkeypatch.setenv('AWE_ARCH_WORKFLOW_FILE_LINES_MAX', '20')
    monkeypatch.setenv('AWE_ARCH_FRONTEND_FILE_LINES_MAX', '20')
    monkeypatch.setenv('AWE_ARCH_DASHBOARD_JS_LINES_MAX', '20')
    monkeypatch.setenv('AWE_ARCH_PROMPT_BUILDER_COUNT_MAX', '1')
    monkeypatch.setenv('AWE_ARCH_ADAPTER_RUNTIME_RAISE_MAX', '0')

    original_relative_to = Path.relative_to
    original_read_text = Path.read_text

    def fake_relative_to(self, *args, **kwargs):  # noqa: ANN001
        if self.name == 'trigger.py':
            raise ValueError('forced')
        return original_relative_to(self, *args, **kwargs)

    def fake_read_text(self, *args, **kwargs):  # noqa: ANN001
        if self.name == 'broken.py':
            raise OSError('forced')
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'relative_to', fake_relative_to)
    monkeypatch.setattr(Path, 'read_text', fake_read_text)
    (root / 'broken.py').write_text('print(1)\n', encoding='utf-8')

    result = run_architecture_audit(cwd=root, evolution_level=3)
    kinds = {str(v.get('kind')) for v in result.violations}
    assert result.enabled is True
    assert result.passed is False
    assert result.reason == 'architecture_threshold_exceeded'
    assert 'python_file_too_large' in kinds
    assert 'python_mixed_responsibilities' in kinds
    assert 'service_monolith_too_large' in kinds
    assert 'workflow_monolith_too_large' in kinds
    assert 'prompt_assembly_hotspot' in kinds
    assert 'adapter_runtime_raise_detected' in kinds
    assert 'frontend_file_too_large' in kinds
    assert 'dashboard_monolith_too_large' in kinds
    assert 'script_cross_platform_gap' in kinds
    assert result.scanned_files > 0


def test_build_environment_context_contains_workspace_excerpt(tmp_path: Path):
    (tmp_path / 'src').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'src' / 'main.py').write_text('print("x")\n', encoding='utf-8')
    context = build_environment_context(
        cwd=tmp_path,
        test_command='py -m pytest -q',
        lint_command='py -m ruff check .',
    )
    assert 'Execution context:' in context
    assert 'Workspace excerpt:' in context
