"""JSON-file backed todo store."""

from __future__ import annotations

import json
from pathlib import Path


def load(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path: Path, items: list[dict]) -> None:
    Path(path).write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def add(path: Path, title: str) -> dict:
    items = load(path)
    item = {"id": len(items) + 1, "title": title}
    items.append(item)
    save(path, items)
    return item
