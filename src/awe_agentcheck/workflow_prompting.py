from __future__ import annotations

from pathlib import Path
from string import Template


def inject_prompt_extras(
    *,
    base: str,
    environment_context: str | None,
    strategy_hint: str | None,
    memory_context: str | None = None,
) -> str:
    text = str(base or '')
    env = str(environment_context or '').strip()
    if env:
        text = f'{text}\n{env}'
    memory = str(memory_context or '').strip()
    if memory:
        text = f'{text}\n{memory}'
    hint = str(strategy_hint or '').strip()
    if hint:
        text = f'{text}\nStrategy shift hint: {hint}'
    return text


def load_prompt_template(
    *,
    template_name: str,
    template_dir: Path,
    cache: dict[str, Template],
) -> Template:
    key = str(template_name or '').strip()
    if not key:
        raise ValueError('template_name is required')
    cached = cache.get(key)
    if cached is not None:
        return cached
    safe_name = Path(key).name
    if safe_name != key:
        raise ValueError(f'invalid prompt template name: {template_name}')
    template_path = (template_dir / safe_name).resolve(strict=False)
    base_dir = template_dir.resolve(strict=False)
    try:
        template_path.relative_to(base_dir)
    except ValueError as exc:
        raise ValueError(f'invalid prompt template path: {template_name}') from exc
    text = template_path.read_text(encoding='utf-8')
    template = Template(text)
    cache[key] = template
    return template


def render_prompt_template(
    *,
    template_name: str,
    template_dir: Path,
    cache: dict[str, Template],
    fields: dict[str, object],
) -> str:
    template = load_prompt_template(
        template_name=template_name,
        template_dir=template_dir,
        cache=cache,
    )
    normalized = {str(k): ('' if v is None else str(v)) for k, v in fields.items()}
    return template.safe_substitute(normalized)
