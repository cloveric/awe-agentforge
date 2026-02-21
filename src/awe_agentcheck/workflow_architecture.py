from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re


@dataclass(frozen=True)
class ArchitectureAuditResult:
    enabled: bool
    passed: bool
    mode: str
    reason: str
    thresholds: dict[str, int]
    violations: list[dict[str, object]]
    scanned_files: int


def default_ignore_dirs() -> set[str]:
    return {
        '.git',
        '.agents',
        '.venv',
        '__pycache__',
        '.pytest_cache',
        '.ruff_cache',
        'node_modules',
    }


def workspace_tree_excerpt(root: Path, *, max_depth: int, max_entries: int) -> str:
    ignore_dirs = default_ignore_dirs()
    lines: list[str] = []
    visited = 0
    truncated = False

    def walk(path: Path, depth: int) -> None:
        nonlocal visited, truncated
        if truncated or depth > max_depth:
            return
        try:
            entries = sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        except OSError:
            return

        for entry in entries:
            if visited >= max_entries:
                truncated = True
                return
            is_dir = entry.is_dir()
            if is_dir and entry.name in ignore_dirs:
                continue
            try:
                rel = entry.relative_to(root).as_posix()
            except ValueError:
                rel = entry.name
            indent = '  ' * max(0, depth)
            marker = 'D' if is_dir else 'F'
            lines.append(f'{indent}- [{marker}] {rel}')
            visited += 1
            if is_dir:
                walk(entry, depth + 1)

    if root.exists() and root.is_dir():
        walk(root, 0)
    if not lines:
        return '- [n/a] workspace tree unavailable'
    if truncated:
        lines.append(f'- [truncated] showing first {max_entries} entries')
    return '\n'.join(lines)


def build_environment_context(*, cwd: Path, test_command: str | list[str], lint_command: str | list[str]) -> str:
    root = Path(cwd).resolve(strict=False)
    tree_excerpt = workspace_tree_excerpt(root, max_depth=2, max_entries=42)
    lines = [
        'Execution context:',
        f'- Workspace root: {root}',
        f'- Test command: {test_command}',
        f'- Lint command: {lint_command}',
        '- Constraints: cite repo-relative evidence paths for key findings and edits.',
        '- Constraints: avoid hidden bypasses/default secrets; keep changes scoped and testable.',
        '- Workspace excerpt:',
        tree_excerpt,
    ]
    return '\n'.join(lines)


def architecture_thresholds_for_level(level: int) -> dict[str, int]:
    normalized = max(0, min(3, int(level)))
    if normalized >= 3:
        thresholds = {
            'python_file_lines_max': 800,
            'frontend_file_lines_max': 1500,
            'python_responsibility_keywords_max': 6,
            'service_file_lines_max': 2800,
            'workflow_file_lines_max': 1800,
            'dashboard_js_lines_max': 2600,
            'prompt_builder_count_max': 8,
            'adapter_runtime_raise_max': 0,
        }
    elif normalized >= 2:
        thresholds = {
            'python_file_lines_max': 1000,
            'frontend_file_lines_max': 2000,
            'python_responsibility_keywords_max': 8,
            'service_file_lines_max': 3500,
            'workflow_file_lines_max': 2200,
            'dashboard_js_lines_max': 3200,
            'prompt_builder_count_max': 10,
            'adapter_runtime_raise_max': 0,
        }
    else:
        thresholds = {
            'python_file_lines_max': 1200,
            'frontend_file_lines_max': 2500,
            'python_responsibility_keywords_max': 10,
            'service_file_lines_max': 4500,
            'workflow_file_lines_max': 2600,
            'dashboard_js_lines_max': 3800,
            'prompt_builder_count_max': 14,
            'adapter_runtime_raise_max': 0,
        }

    env_map = {
        'python_file_lines_max': ('AWE_ARCH_PYTHON_FILE_LINES_MAX', 10),
        'frontend_file_lines_max': ('AWE_ARCH_FRONTEND_FILE_LINES_MAX', 10),
        'python_responsibility_keywords_max': ('AWE_ARCH_RESPONSIBILITY_KEYWORDS_MAX', 1),
        'service_file_lines_max': ('AWE_ARCH_SERVICE_FILE_LINES_MAX', 10),
        'workflow_file_lines_max': ('AWE_ARCH_WORKFLOW_FILE_LINES_MAX', 10),
        'dashboard_js_lines_max': ('AWE_ARCH_DASHBOARD_JS_LINES_MAX', 10),
        'prompt_builder_count_max': ('AWE_ARCH_PROMPT_BUILDER_COUNT_MAX', 1),
        'adapter_runtime_raise_max': ('AWE_ARCH_ADAPTER_RUNTIME_RAISE_MAX', 0),
    }
    for key, (env_name, minimum) in env_map.items():
        raw = str(os.getenv(env_name, '') or '').strip()
        if not raw:
            continue
        try:
            parsed = int(raw)
        except ValueError:
            continue
        thresholds[key] = max(minimum, parsed)
    return thresholds


def architecture_audit_mode(level: int) -> str:
    raw = str(os.getenv('AWE_ARCH_AUDIT_MODE', '') or '').strip().lower()
    if raw in {'off', 'warn', 'hard'}:
        return raw
    normalized = max(0, min(3, int(level)))
    return 'warn' if normalized >= 1 else 'off'


def run_architecture_audit(*, cwd: Path, evolution_level: int) -> ArchitectureAuditResult:
    level = max(0, min(3, int(evolution_level)))
    if level < 1:
        return ArchitectureAuditResult(
            enabled=False,
            passed=True,
            mode='off',
            reason='skipped',
            thresholds={},
            violations=[],
            scanned_files=0,
        )

    root = Path(cwd).resolve(strict=False)
    thresholds = architecture_thresholds_for_level(level)
    mode = architecture_audit_mode(level)
    ignore_dirs = default_ignore_dirs()
    frontend_ext = {'.html', '.css', '.scss', '.js', '.jsx', '.ts', '.tsx', '.vue', '.svelte'}
    responsibility_keywords = (
        'sandbox',
        'policy',
        'analytics',
        'evolution',
        'proposal',
        'merge',
        'history',
        'review',
        'workflow',
        'database',
        'api',
        'session',
        'theme',
        'avatar',
        'benchmark',
        'preflight',
        'runtime',
    )
    violations: list[dict[str, object]] = []
    scanned_files = 0

    if not root.exists() or not root.is_dir():
        return ArchitectureAuditResult(
            enabled=True,
            passed=False,
            mode=mode,
            reason='architecture_audit_workspace_missing',
            thresholds=thresholds,
            violations=[],
            scanned_files=0,
        )

    for dirpath, dirs, files in os.walk(root):
        base = Path(dirpath)
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        for name in files:
            path = base / name
            ext = path.suffix.lower()
            if ext not in {'.py', *frontend_ext}:
                continue
            scanned_files += 1
            try:
                file_text = path.read_text(encoding='utf-8', errors='ignore')
            except OSError:
                continue
            line_count = int(file_text.count('\n') + 1) if file_text else 0
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                rel = path.as_posix()
            if ext == '.py' and line_count > int(thresholds['python_file_lines_max']):
                violations.append(
                    {
                        'kind': 'python_file_too_large',
                        'path': rel,
                        'lines': int(line_count),
                        'limit': int(thresholds['python_file_lines_max']),
                        'suggestion': 'Split responsibilities into smaller modules.',
                    }
                )
            if ext == '.py' and line_count > max(300, int(thresholds['python_file_lines_max']) // 2):
                lowered = file_text.lower()
                responsibility_hits = sum(1 for k in responsibility_keywords if k in lowered)
                responsibility_limit = int(thresholds['python_responsibility_keywords_max'])
                if responsibility_hits > responsibility_limit:
                    violations.append(
                        {
                            'kind': 'python_mixed_responsibilities',
                            'path': rel,
                            'lines': int(line_count),
                            'responsibility_hits': int(responsibility_hits),
                            'limit': int(responsibility_limit),
                            'suggestion': 'Extract domain concerns into focused modules.',
                        }
                    )
            if rel == 'src/awe_agentcheck/service.py' and line_count > int(thresholds['service_file_lines_max']):
                violations.append(
                    {
                        'kind': 'service_monolith_too_large',
                        'path': rel,
                        'lines': int(line_count),
                        'limit': int(thresholds['service_file_lines_max']),
                        'suggestion': 'Split service lifecycle/orchestration concerns into focused modules.',
                    }
                )
            if rel == 'src/awe_agentcheck/workflow.py' and line_count > int(thresholds['workflow_file_lines_max']):
                violations.append(
                    {
                        'kind': 'workflow_monolith_too_large',
                        'path': rel,
                        'lines': int(line_count),
                        'limit': int(thresholds['workflow_file_lines_max']),
                        'suggestion': 'Extract prompt/phase controllers into smaller workflow modules.',
                    }
                )
            if rel in {'src/awe_agentcheck/workflow.py', 'src/awe_agentcheck/service.py'} and ext == '.py':
                prompt_builder_hits = int(file_text.count('_prompt('))
                if prompt_builder_hits > int(thresholds['prompt_builder_count_max']):
                    violations.append(
                        {
                            'kind': 'prompt_assembly_hotspot',
                            'path': rel,
                            'prompt_builder_hits': prompt_builder_hits,
                            'limit': int(thresholds['prompt_builder_count_max']),
                            'suggestion': 'Move prompt templates into dedicated files and compose with data-only bindings.',
                        }
                    )
            if rel in {'src/awe_agentcheck/adapters.py', 'src/awe_agentcheck/adapters/runner.py'}:
                runtime_raise_hits = len(re.findall(r'raise\s+RuntimeError\s*\(', file_text))
                if runtime_raise_hits > int(thresholds['adapter_runtime_raise_max']):
                    violations.append(
                        {
                            'kind': 'adapter_runtime_raise_detected',
                            'path': rel,
                            'runtime_raise_hits': int(runtime_raise_hits),
                            'limit': int(thresholds['adapter_runtime_raise_max']),
                            'suggestion': 'Return structured adapter errors and let workflow decide retry/fallback/gate.',
                        }
                    )
            if ext in frontend_ext and line_count > int(thresholds['frontend_file_lines_max']):
                violations.append(
                    {
                        'kind': 'frontend_file_too_large',
                        'path': rel,
                        'lines': int(line_count),
                        'limit': int(thresholds['frontend_file_lines_max']),
                        'suggestion': 'Split UI into smaller files/components.',
                    }
                )
            if rel == 'web/assets/dashboard.js' and line_count > int(thresholds['dashboard_js_lines_max']):
                violations.append(
                    {
                        'kind': 'dashboard_monolith_too_large',
                        'path': rel,
                        'lines': int(line_count),
                        'limit': int(thresholds['dashboard_js_lines_max']),
                        'suggestion': 'Split dashboard runtime by panel/feature modules.',
                    }
                )

    scripts_dir = root / 'scripts'
    if scripts_dir.exists() and scripts_dir.is_dir():
        ps1_files = {
            p.stem.lower()
            for p in scripts_dir.glob('*.ps1')
            if p.is_file()
        }
        sh_files = {
            p.stem.lower()
            for p in scripts_dir.glob('*.sh')
            if p.is_file()
        }
        missing_shell = sorted(name for name in ps1_files if name not in sh_files)
        if missing_shell:
            violations.append(
                {
                    'kind': 'script_cross_platform_gap',
                    'path': 'scripts',
                    'missing_shell_variants': missing_shell,
                    'suggestion': 'Add matching .sh wrappers for cross-platform usage.',
                }
            )

    if not violations:
        reason = 'passed'
    elif mode == 'hard':
        reason = 'architecture_threshold_exceeded'
    else:
        reason = 'architecture_threshold_warning'
    return ArchitectureAuditResult(
        enabled=True,
        passed=not violations,
        mode=mode,
        reason=reason,
        thresholds=thresholds,
        violations=violations,
        scanned_files=int(scanned_files),
    )
