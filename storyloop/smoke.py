"""Per-change acceptance suites (layer 2): written blind from the contract, proven RED, protected.

Layout: `acceptance/changes/<change-id>/` holds the suite and its manifest. Repositories that
started before the rename keep their `acceptance/smoke/<change-id>/` suites; those are still
recognized so their evidence and `storyloop ci` reruns keep working. The project-level system
smoke suite (layer 1) lives at `acceptance/smoke/` itself, see system_smoke.py.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .models import StoryLoopError, fields, identifier, require
from .state_format import criterion_expects_red, criterion_tier
from .path_security import repository_relative, safe_target
from .project_profile import is_ci_path
from .storage import load_json, write_json, write_text_atomic

SUITE_DIR = "acceptance/changes"
LEGACY_SUITE_DIR = "acceptance/smoke"
PROTECTION_DIR = ".storyloop/state/suites"  # state/acceptance holds human acceptance records
LEGACY_PROTECTION_DIR = ".storyloop/state/smoke"
RED_EVIDENCE = "acceptance-red.json"
RESULT_EVIDENCE = "acceptance-result.json"


def suite_relative(project: Path, change_id: str) -> str:
    """Repository-relative suite directory; a pre-rename suite keeps its legacy location."""
    change_id = identifier(change_id, "change id")
    legacy = Path(project).resolve() / LEGACY_SUITE_DIR / change_id
    return f"{LEGACY_SUITE_DIR}/{change_id}" if legacy.is_dir() else f"{SUITE_DIR}/{change_id}"


def suite_root(project: Path, change_id: str) -> Path:
    return Path(project).resolve() / suite_relative(project, change_id)


def protection_path(project: Path, change_id: str) -> Path:
    project = Path(project).resolve()
    name = f"{identifier(change_id, 'change id')}.json"
    legacy = project / LEGACY_PROTECTION_DIR / name
    return legacy if legacy.is_file() else project / PROTECTION_DIR / name


def without_manifest_echo(files: object) -> object:
    """The manifest travels separately; an agent that also lists manifest.json among its files only echoes it."""
    if not isinstance(files, list):
        return files
    return [item for item in files if not (isinstance(item, dict) and item.get("path") == "manifest.json")]


def validate_suite(change: dict, files: object, manifest: object) -> None:
    """Validate generated suite content before any of it is written to the repository."""
    _validate_manifest(manifest, change)
    _validate_files(without_manifest_echo(files))


def install_smoke_suite(
    project: Path, change: dict, files: list[dict], manifest: dict, replace: bool = False,
) -> dict:
    """Validate then atomically install an externally owned smoke suite.

    `replace` is for a repair by the suite's owner: the previous files are removed first, and
    the caller must re-prove RED for the new suite before any green run counts.
    """
    project = Path(project).resolve()
    change_id = identifier(change["change_id"], "change id")
    require(change["status"] in {"APPROVED", "RUNNING"} and change["approval"],
            "Change must be approved before smoke generation")
    normalized = _validate_manifest(manifest, change)
    sources = _validate_files(files)
    root = safe_target(project, suite_relative(project, change_id), "acceptance suite root", target_directory=True)
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


def load_smoke_manifest(project: Path, change: dict) -> dict:
    """Load a smoke manifest only when every protected byte still matches."""
    project = Path(project).resolve()
    change_id = identifier(change["change_id"], "change id")
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
    require(current == digests, "Protected smoke suite was modified: "
            f"missing={sorted(set(digests) - set(current))} extra={sorted(set(current) - set(digests))} "
            f"changed={sorted(k for k in set(digests) & set(current) if digests[k] != current[k])}")
    manifest = load_json(root / "manifest.json")
    return _validate_manifest(manifest, change)


def smoke_digests(project: Path, change: dict) -> dict[str, str]:
    change_id = identifier(change["change_id"], "change id")
    load_smoke_manifest(project, change)
    value = load_json(_protection_path(Path(project).resolve(), change_id))
    return dict(value["digests"])


def _validate_manifest(manifest: object, change: dict) -> dict:
    fields(manifest, {"version", "tests"})
    require(manifest["version"] == 1 and isinstance(manifest["tests"], list)
            and bool(manifest["tests"]), "Smoke manifest needs tests")
    automated = {
        criterion["id"]: criterion
        for story in change["product"]["stories"]
        for criterion in story["acceptance"] if criterion["kind"] == "automated"
    }
    seen: set[str] = set()
    covered: set[str] = set()
    for test in manifest["tests"]:
        _validate_manifest_test(test, automated, seen)
        covered.update(test["criteria"])
    require(set(automated) <= covered, "Automated acceptance criteria are uncovered")
    return manifest


def _validate_manifest_test(test: dict, automated: dict, seen: set) -> None:
    fields(test, {"id", "criteria", "command", "timeout_seconds"}, {"tier"})
    test_id = identifier(test["id"], "smoke test id")
    require(test_id not in seen, "Duplicate smoke test id")
    seen.add(test_id)
    require(isinstance(test["criteria"], list)
            and all(isinstance(item, str) for item in test["criteria"]),
            "Smoke criteria must be a list")
    require(set(test["criteria"]) <= set(automated), "Smoke test references unknown criterion")
    tier = test.get("tier", "fast")
    require(tier in {"fast", "heavy"}, "Smoke test tier must be fast or heavy")
    criteria = [automated[item] for item in test["criteria"]]
    require(len({criterion_expects_red(c) for c in criteria}) <= 1,
            "One test cannot mix criteria with different expected pre-implementation states")
    require(all(criterion_tier(c) == tier for c in criteria),
            "A test's tier must match its criteria's oracle_class (auto-fast → fast, auto-heavy → heavy)")
    command = test["command"]
    require(isinstance(command, list) and bool(command),
            "Smoke command must be a nonempty argv array")
    require(len(command) <= 100 and all(isinstance(arg, str) and bool(arg) for arg in command),
            "Smoke command argv is invalid")
    require(type(test["timeout_seconds"]) is int
            and 1 <= test["timeout_seconds"] <= 3600, "Invalid smoke timeout")


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
    return suite_root(project, change_id)


def _protection_path(project: Path, change_id: str) -> Path:
    return protection_path(project, change_id)


def _write_text(path: Path, content: str) -> None:
    write_text_atomic(path, content)


def _sha256(path: Path) -> str:
    """Content digest with line endings normalized to LF: Git on Windows (core.autocrlf) rewrites
    CRLF on checkout and merge, which is not a modification of the suite."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
