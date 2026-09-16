"""Evidence integrity and sanitization for persisted workflow results."""
from __future__ import annotations

import hashlib
from pathlib import Path
from .processutil import redact


def sanitized(value: object) -> object:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [sanitized(item) for item in value]
    if isinstance(value, dict):
        return {key: sanitized(item) for key, item in value.items()}
    return value


def valid_evidence(project: Path, record: dict) -> bool:
    """Success needs complete, unchanged artifacts, not just state booleans."""
    evidence = record.get("evidence", [])
    hashes = record.get("evidence_hashes", {})
    if not evidence or set(evidence) != set(hashes):
        return False
    for relative in evidence:
        path = project / relative
        if not path.resolve().is_relative_to((project / ".storyloop/evidence").resolve()):
            return False
        if path.is_symlink() or not path.is_file():
            return False
        try:
            if hashlib.sha256(path.read_bytes()).hexdigest() != hashes[relative]:
                return False
        except OSError:
            return False
    return bool(record.get("last_checks")) and record.get("review", {}).get("verdict") == "pass"
