"""Independent core-flow smoke acceptance suites."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .changes import load_change
from .models import StoryLoopError, fields, identifier, require
from .path_security import repository_relative, safe_target
from .project_profile import is_ci_path
from .storage import load_json, write_json, write_text_atomic


def install_smoke_suite(
    project: Path, change_id: str, files: list[dict], manifest: dict, replace: bool = False,
) -> dict:
    """Validate then atomically install an externally owned smoke suite.

    `replace` is for a repair by the suite's owner: the previous files are removed first, and
    the caller must re-prove RED for the new suite before any green run counts.
    """
    project = Path(project).resolve()
    change = load_change(project, change_id)
    require(change["status"] in {"APPROVED", "RUNNING"} and change["approval"],
            "Change must be approved before smoke generation")
    normalized = _validate_manifest(manifest, change)
    sources = _validate_files(files)
    root_relative = f"acceptance/smoke/{identifier(change_id, 'change id')}"
    root = safe_target(project, root_relative, "smoke root", target_directory=True)
    root.mkdir(parents=True, exist_ok=True)
    if replace:
        for stale in sorted(root.rglob("*"), reverse=True):
            if stale.is_file() or stale.is_symlink():
                stale.unlink()
            elif stale.is_dir():
                stale.rmdir()
    for relative, content in sources:
        target = safe_target(root, relative, "smoke path")
        if target.exists():
            require(target.is_file() and not target.is_symlink()
                    and target.read_text(encoding="utf-8") == content,
                    "Existing smoke source is protected")
        else:
            _write_text(target, content)
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        require(load_json(manifest_path) == normalized, "Existing smoke manifest is protected")
    else:
        write_json(manifest_path, normalized, max_bytes=1_000_000)
    digests = {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(root.rglob("*")) if path.is_file()
    }
    protection = {"version": 1, "change_id": change_id, "digests": digests}
    write_json(_protection_path(project, change_id), protection, max_bytes=2_000_000)
    return {"status": "SMOKE_INSTALLED", "change_id": change_id, "digests": digests}


def load_smoke_manifest(project: Path, change_id: str) -> dict:
    """Load a smoke manifest only when every protected byte still matches."""
    project = Path(project).resolve()
    root = _suite_root(project, change_id)
    protection = load_json(_protection_path(project, change_id))
    require(protection.get("version") == 1 and protection.get("change_id") == change_id,
            "Invalid smoke protection state")
    digests = protection.get("digests")
    require(isinstance(digests, dict) and bool(digests), "Invalid smoke digests")
    current = {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(root.rglob("*")) if path.is_file() and not path.is_symlink()
    }
    require(current == digests, "Protected smoke suite was modified")
    manifest = load_json(root / "manifest.json")
    return _validate_manifest(manifest, load_change(project, change_id))


def smoke_digests(project: Path, change_id: str) -> dict[str, str]:
    load_smoke_manifest(project, change_id)
    value = load_json(_protection_path(Path(project).resolve(), change_id))
    return dict(value["digests"])


def _validate_manifest(manifest: object, change: dict) -> dict:
    fields(manifest, {"version", "tests"})
    require(manifest["version"] == 1 and isinstance(manifest["tests"], list)
            and bool(manifest["tests"]), "Smoke manifest needs tests")
    automated = {
        criterion["id"]
        for story in change["product"]["stories"]
        for criterion in story["acceptance"] if criterion["kind"] == "automated"
    }
    seen: set[str] = set()
    covered: set[str] = set()
    for test in manifest["tests"]:
        fields(test, {"id", "criteria", "command", "timeout_seconds"})
        test_id = identifier(test["id"], "smoke test id")
        require(test_id not in seen, "Duplicate smoke test id")
        seen.add(test_id)
        require(isinstance(test["criteria"], list)
                and all(isinstance(item, str) for item in test["criteria"]),
                "Smoke criteria must be a list")
        require(set(test["criteria"]) <= automated, "Smoke test references unknown criterion")
        command = test["command"]
        require(isinstance(command, list) and bool(command),
                "Smoke command must be a nonempty argv array")
        require(len(command) <= 100 and all(isinstance(arg, str) and bool(arg) for arg in command),
                "Smoke command argv is invalid")
        require(type(test["timeout_seconds"]) is int
                and 1 <= test["timeout_seconds"] <= 3600, "Invalid smoke timeout")
        covered.update(test["criteria"])
    require(automated <= covered, "Automated acceptance criteria are uncovered")
    return manifest


def _validate_files(files: object) -> list[tuple[str, str]]:
    require(isinstance(files, list) and bool(files), "Smoke generation needs source files")
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in files:
        fields(item, {"path", "content"})
        relative = _safe_relative(item["path"])
        require(relative != "manifest.json", "manifest.json is reserved")
        require(relative not in seen, "Duplicate smoke source path")
        require(isinstance(item["content"], str)
                and len(item["content"].encode("utf-8")) <= 2_000_000,
                "Invalid smoke source content")
        seen.add(relative)
        result.append((relative, item["content"]))
    return result


def _safe_relative(value: object) -> str:
    normalized = repository_relative(value, "Smoke path")
    require(not is_ci_path(normalized),
            "Unsafe smoke path or CI output")
    return normalized


def _suite_root(project: Path, change_id: str) -> Path:
    return project / "acceptance" / "smoke" / identifier(change_id, "change id")


def _protection_path(project: Path, change_id: str) -> Path:
    return project / ".storyloop" / "state" / "smoke" / f"{identifier(change_id, 'change id')}.json"


def _write_text(path: Path, content: str) -> None:
    write_text_atomic(path, content)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
