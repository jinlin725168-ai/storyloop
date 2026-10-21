"""Bounded CI result artifacts used by cross-change metrics."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from pathlib import Path

from .baseline import _git
from .models import StoryLoopError, require
from .report_evidence import _read_object
from .storage import write_json


MAX_CI_RESULT_BYTES = 1_000_000
COMMIT = re.compile(r"[0-9a-fA-F]{40,64}")


def record_ci_result(path: Path, project: Path, result: dict) -> None:
    """Write one CI result beside existing caller-managed build artifacts."""
    path = Path(path).absolute()
    require(path.parent.is_dir(), "CI result parent directory must already exist")
    value = {
        "version": 1,
        "change_id": result["change_id"],
        "status": result["status"],
        "commit": _git(Path(project).resolve(), "rev-parse", "HEAD").strip(),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "layers": {
            "acceptance": result["acceptance"]["status"],
            "smoke": result["smoke"]["status"],
        },
    }
    write_json(path, value, max_bytes=MAX_CI_RESULT_BYTES)


def load_ci_results(
    project: Path, folder: Path, records: list[dict],
) -> tuple[dict[str, dict], list[dict]]:
    """Load latest related CI artifact per known change from one explicit directory."""
    root = _safe_folder(folder)
    if root is None:
        return {}, []
    merges = {_change_id(record): _merge_commit(record) for record in records}
    selected: dict[str, tuple[datetime, dict]] = {}
    missing: list[dict] = []
    try:
        candidates = sorted(root.glob("ci-*.json"))
    except OSError:
        return {}, []
    for path in candidates:
        value = _read_object(root, path, MAX_CI_RESULT_BYTES)
        change_id = value.get("change_id") if isinstance(value, dict) else None
        if not isinstance(change_id, str) or change_id not in merges:
            continue
        timestamp = _valid_result(value)
        if timestamp is None:
            _missing(missing, change_id, "UNREADABLE")
            continue
        if not merges[change_id]:
            _missing(missing, change_id, "CI_MERGE_COMMIT_ABSENT")
            continue
        related = _related(project, merges[change_id], value["commit"])
        if related is None:
            _missing(missing, change_id, "UNREADABLE")
        elif not related:
            _missing(missing, change_id, "CI_COMMIT_UNRELATED")
        elif change_id not in selected or timestamp > selected[change_id][0]:
            selected[change_id] = (timestamp, value)
    return {change_id: item[1] for change_id, item in selected.items()}, missing


def _safe_folder(folder: Path) -> Path | None:
    folder = Path(folder)
    try:
        if folder.is_symlink() or not folder.is_dir():
            return None
        return folder.resolve()
    except OSError:
        return None


def _valid_result(value: dict) -> datetime | None:
    required = ("change_id", "status", "commit", "recorded_at", "layers")
    if (type(value.get("version")) is not int or value["version"] != 1
            or not all(key in value for key in required)):
        return None
    if (not isinstance(value["change_id"], str) or value["status"] not in ("PASSED", "FAILED")
            or not isinstance(value["layers"], dict)
            or not isinstance(value["commit"], str)
            or COMMIT.fullmatch(value["commit"]) is None):
        return None
    layers = value["layers"]
    if (layers.get("acceptance") not in ("PASSED", "FAILED", "ERROR")
            or layers.get("smoke") not in ("PASSED", "FAILED", "ERROR", "SKIPPED")):
        return None
    try:
        parsed = datetime.fromisoformat(str(value["recorded_at"]).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _related(project: Path, merge: str, commit: str) -> bool | None:
    if COMMIT.fullmatch(merge) is None:
        return None
    try:
        # A merge is an ancestor exactly when no commit reachable from it is
        # excluded from the candidate's history. This query returns at most one
        # commit and keeps unrelated histories distinct from Git process errors.
        return not _git(Path(project).resolve(), "rev-list", "--max-count=1",
                        merge, "--not", commit).strip()
    except (StoryLoopError, OSError):
        return None


def _change_id(record: dict) -> str:
    value = record.get("change_id")
    return value if isinstance(value, str) else ""


def _merge_commit(record: dict) -> str | None:
    commits = record.get("commits")
    value = commits.get("merge_commit") if isinstance(commits, dict) else None
    return value if isinstance(value, str) else None


def _missing(missing: list[dict], change_id: str, reason: str) -> None:
    item = {"change_id": change_id, "reason": reason}
    if item not in missing:
        missing.append(item)
