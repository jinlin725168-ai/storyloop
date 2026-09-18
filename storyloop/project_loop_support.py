"""Persistence and integrity helpers for the external project loop."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .baseline import _git
from .storage import write_json


def control_digest(
    project: Path, change_id: str, excluded_evidence: str | None = None,
) -> str:
    # .git/index is deliberately absent: read-only git commands rewrite its stat cache.
    # Staged content is covered semantically by `git diff --cached` below.
    relative_paths = [
        ".git/HEAD", ".git/config", ".git/info/exclude", ".git/packed-refs",
        ".storyloop/config.json", ".storyloop/baseline.json",
        f".storyloop/state/changes/{change_id}.json",
        f".storyloop/state/smoke/{change_id}.json",
        f".storyloop/state/runs/{change_id}.json",
    ]
    for name in ("smoke-red.json", "smoke-result.json"):
        if name != excluded_evidence:
            relative_paths.append(f".storyloop/evidence/{change_id}/{name}")
    digest = hashlib.sha256(_git(project, "rev-parse", "HEAD").encode("utf-8"))
    digest.update(_git(project, "branch", "--show-current").encode("utf-8"))
    digest.update(_git(
        project, "for-each-ref", "--format=%(refname):%(objectname)"
    ).encode("utf-8"))
    digest.update(_git(project, "diff", "--cached", "--name-status").encode("utf-8"))
    digest.update(_path_digest(project, relative_paths).encode("ascii"))
    return digest.hexdigest()


def relative_files(project: Path, roots: list[Path]) -> list[str]:
    return sorted(path.relative_to(project).as_posix() for root in roots
                  for path in root.rglob("*") if path.is_file() and not path.is_symlink())


def save_run(project: Path, state: dict) -> None:
    write_json(run_path(project, state["change_id"]), state, max_bytes=8_000_000)


def run_path(project: Path, change_id: str) -> Path:
    return project / ".storyloop" / "state" / "runs" / f"{change_id}.json"


def set_change_status(project: Path, change: dict, status: str) -> None:
    updated = dict(change)
    updated["status"] = status
    write_json(project / ".storyloop/state/changes" / f"{change['change_id']}.json",
               updated, max_bytes=2_000_000)


def _path_digest(project: Path, relative_paths: list[str]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(relative_paths, key=str.casefold):
        path = project / relative
        digest.update(relative.encode("utf-8") + b"\0")
        if path.is_file() and not path.is_symlink():
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        else:
            digest.update(b"<missing-or-unsafe>")
    return digest.hexdigest()
