from __future__ import annotations

from pathlib import Path
import sys


def _prepend_repo_src_to_syspath() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src = repo_root / 'src'
    if not src.is_dir():
        return
    src_text = str(src)
    normalized = src_text.replace('\\', '/').lower()

    cleaned: list[str] = []
    seen: set[str] = set()

    def add(item: str) -> None:
        text = str(item or '').strip()
        if not text:
            return
        key = text.replace('\\', '/').lower()
        if key in seen:
            return
        seen.add(key)
        cleaned.append(text)

    add(src_text)
    for item in list(sys.path):
        text = str(item or '').strip()
        if not text:
            continue
        key = text.replace('\\', '/').lower()
        if key == normalized:
            continue
        add(text)
    sys.path[:] = cleaned


_prepend_repo_src_to_syspath()
