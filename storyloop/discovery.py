"""Product discussion and non-executing QA preparation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
import os
import uuid

from . import agents
from .models import StoryLoopError, validate_checks, validate_product
from .storage import load_json, locked, write_json


def discuss(project: Path, message: str) -> dict:
    """Update the product through a validated, durable conversation turn."""
    if not isinstance(message, str) or not message.strip():
        raise StoryLoopError("A product discussion message is required.")
    project = project.resolve()
    with locked(project):
        current = load_json(project / "product.json")
        payload = {"product": current, "message": message, "brief": _brief(project),
                   "history": _history(project)}
        result = validate_product(agents.invoke(project, "product", payload))
        _save_history(project, "discuss", {"message": message, "before": current, "after": result})
        write_json(project / "product.json", result)
        _invalidate(project)
        return result


def prepare(project: Path) -> dict:
    """Persist reviewed-shape QA proposals without executing any generated program."""
    project = project.resolve()
    with locked(project):
        product = validate_product(load_json(project / "product.json"))
        if product.get("questions"):
            raise StoryLoopError("Resolve product questions before preparing executable QA.", code="NEEDS_INPUT")
        payload = {"product": product, "brief": _brief(project),
                   "checks": load_json(project / "checks.json"),
                   "existing_workspace_files": _file_names(project / "workspace")}
        result = agents.invoke(project, "prepare", payload)
        if set(result) != {"checks", "qa_files", "workspace_files"}:
            raise StoryLoopError("Prepare response must contain checks, qa_files and workspace_files.")
        checks = validate_checks(result["checks"], product)
        files = _validate_files(project, result)
        _save_history(project, "prepare", {"previous_checks": payload["checks"], "checks": checks})
        _persist_proposal(project, files, checks)
        _invalidate(project)
        return {"status": "PREPARED", "checks": checks,
                "files": [str(path.relative_to(project)) for path, _ in files],
                "next": "Review qa/ and checks.json, then explicitly approve the baseline."}


def _persist_proposal(project: Path, files: list, checks: dict) -> None:
    """Validate first; roll back file contents if any promotion fails."""
    backups = {path: path.read_bytes() if path.exists() else None for path, _ in files}
    previous_checks = load_json(project / "checks.json")
    written = []
    try:
        for path, content in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_text(path, content)
            written.append(path)
        write_json(project / "checks.json", checks)
    except OSError as exc:
        for path in reversed(written):
            if backups[path] is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_text(path, backups[path].decode("utf-8"))
        write_json(project / "checks.json", previous_checks)
        raise StoryLoopError("Could not persist QA proposal; previous file contents restored.") from exc


def _atomic_text(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_files(project: Path, response: dict) -> list[tuple[Path, str]]:
    planned: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for field, directory in [("qa_files", "qa"), ("workspace_files", "workspace")]:
        files = response[field]
        if not isinstance(files, list):
            raise StoryLoopError(f"{field} must be a list.")
        for item in files:
            if not isinstance(item, dict) or set(item) != {"path", "content"}:
                raise StoryLoopError("Generated files require exactly path and content fields.")
            if not isinstance(item["content"], str):
                raise StoryLoopError("Generated file content must be text.")
            path = _safe_file(project / directory, item["path"])
            if path in seen or any(path in prior.parents or prior in path.parents for prior in seen):
                raise StoryLoopError("Duplicate or conflicting generated file path.")
            if directory == "workspace" and path.exists():
                raise StoryLoopError("Preparation cannot overwrite existing workspace files.")
            if path.exists() and not path.is_file():
                raise StoryLoopError("Generated file path names an existing directory.")
            seen.add(path)
            planned.append((path, item["content"]))
    return planned


def _safe_file(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name.strip() or "\\" in name or ":" in name:
        raise StoryLoopError("Generated file path must be a portable relative path.")
    relative = Path(name)
    if relative.is_absolute() or PureWindowsPath(name).drive or ".." in relative.parts:
        raise StoryLoopError("Generated file path escapes its allowed directory.")
    if relative.parts == () or any(part in {".git", ".storyloop"} for part in relative.parts):
        raise StoryLoopError("Generated file path uses a reserved directory.")
    path = root / relative
    for candidate in [root, *path.parents, path]:
        if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
            raise StoryLoopError("Generated file paths cannot contain symlinks or junctions.")
    if not path.resolve().is_relative_to(root.resolve()):
        raise StoryLoopError("Generated file path escapes its allowed directory.")
    return path


def _brief(project: Path) -> str:
    path = project / "brief.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _file_names(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)).replace("\\", "/")
                  for path in root.rglob("*") if path.is_file() and not path.is_symlink())


def _history(project: Path) -> list[dict]:
    paths = sorted((project / ".storyloop" / "history").glob("*.json"))[-10:]
    return [load_json(path) for path in paths]


def _save_history(project: Path, operation: str, value: dict) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    path = project / ".storyloop" / "history" / f"{timestamp}-{uuid.uuid4().hex[:8]}.json"
    write_json(path, {"operation": operation, "timestamp": timestamp, **value})


def _invalidate(project: Path) -> None:
    (project / ".storyloop" / "approval.json").unlink(missing_ok=True)
