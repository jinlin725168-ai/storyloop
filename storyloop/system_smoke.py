"""Layer 1: the project-level system smoke suite that every change must keep green.

`acceptance/smoke/manifest.json` lists the core flows of the whole system (login, menus, each
capability's main path). The suite accumulates across changes and is protected: a feature
change that touches it is a boundary violation; it only changes through the smoke maintenance
step, where the acceptance role proposes and a human applies. It runs on every attempt, in
`storyloop ci` and in `base-update`.

Legacy per-change suites (`acceptance/smoke/<change-id>/`, pre-rename) share the directory
and are ignored here: they belong to their changes, not to the system suite.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import tempfile

from .models import StoryLoopError, fields, require
from .path_security import repository_relative, safe_target
from .project_profile import is_ci_path
from .storage import load_json, write_json, write_text_atomic

SYSTEM_SMOKE_DIR = "acceptance/smoke"
PROTECTION = ".storyloop/state/system-smoke.json"
LEGACY_CHANGE_DIR = re.compile(r"^[A-Z]+-\d{8}-\d{3}$")
MAX_FILES = 200
MAX_FILE_BYTES = 512_000


def is_system_smoke_path(relative: str) -> bool:
    """True for files of the system suite, false for legacy per-change directories under it."""
    if not relative.startswith(SYSTEM_SMOKE_DIR + "/"):
        return False
    first = relative[len(SYSTEM_SMOKE_DIR) + 1:].split("/", 1)[0]
    return not LEGACY_CHANGE_DIR.match(first)


def install_system_smoke(project: Path, files: list[dict], manifest: dict) -> dict:
    """Replace the system suite atomically and record its protection digests."""
    project = Path(project).resolve()
    normalized = validate_manifest(manifest)
    sources = _validate_files(files)
    root = safe_target(project, SYSTEM_SMOKE_DIR, "system smoke root", target_directory=True)
    root.mkdir(parents=True, exist_ok=True)
    for path in _suite_files(project):
        path.unlink()
    for relative, content in sources:
        target = safe_target(root, relative, "system smoke path")
        target.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(target, content)
    write_json(root / "manifest.json", normalized, max_bytes=1_000_000)
    digests = _digests(project)
    write_json(project / PROTECTION, {"version": 1, "digests": digests}, max_bytes=2_000_000)
    return {"status": "SYSTEM_SMOKE_INSTALLED", "digests": digests}


def load_system_smoke(project: Path) -> dict | None:
    """The manifest when a protected suite exists and every byte still matches; None when absent."""
    project = Path(project).resolve()
    manifest_path = project / SYSTEM_SMOKE_DIR / "manifest.json"
    protection_path = project / PROTECTION
    if not manifest_path.is_file() and not protection_path.is_file():
        return None
    require(manifest_path.is_file() and protection_path.is_file(),
            "System smoke suite and its protection state must exist together")
    protection = load_json(protection_path)
    require(protection.get("version") == 1 and isinstance(protection.get("digests"), dict),
            "Invalid system smoke protection state")
    current = _digests(project)
    require(current == protection["digests"],
            "Protected system smoke suite was modified: " + _describe_drift(protection["digests"], current))
    return validate_manifest(load_json(manifest_path))


def system_smoke_digests(project: Path) -> dict[str, str]:
    load_system_smoke(project)
    return dict(load_json(Path(project).resolve() / PROTECTION)["digests"])


def run_system_smoke_isolated(project: Path) -> dict:
    """Run the protected system suite in a disposable Git-visible candidate; SKIPPED when absent."""
    from .smoke_runner import _copy_candidate, _run_test  # noqa: PLC0415  (avoid an import cycle)
    from .boundaries import capture_snapshot, snapshot_changes

    project = Path(project).resolve()
    manifest = load_system_smoke(project)
    if manifest is None:
        return {"status": "SKIPPED", "reason": "NO_SYSTEM_SMOKE_SUITE", "results": []}
    before = capture_snapshot(project, include_control=True, include_ignored=True)
    try:
        with tempfile.TemporaryDirectory(prefix="storyloop-system-smoke-") as folder:
            candidate = Path(folder)
            _copy_candidate(project, candidate)
            results = [_run_test(candidate, {**item, "criteria": []}) for item in manifest["tests"]]
    finally:
        if snapshot_changes(before, capture_snapshot(project, include_control=True, include_ignored=True)):
            raise StoryLoopError("System smoke modified the source project", code="WORKSPACE_TAINTED")
    if any(item["status"] == "ERROR" for item in results):
        status = "ERROR"
    elif any(item["status"] == "FAIL" for item in results):
        status = "FAILED"
    else:
        status = "PASSED"
    return {"status": status, "results": results, "suite_digests": system_smoke_digests(project)}


def validate_manifest(manifest: object) -> dict:
    require(isinstance(manifest, dict), "System smoke manifest must be an object")
    fields(manifest, {"version", "tests"})
    require(manifest["version"] == 1 and isinstance(manifest["tests"], list) and bool(manifest["tests"]),
            "System smoke manifest needs tests")
    seen: set[str] = set()
    tests = []
    for test in manifest["tests"]:
        fields(test, {"id", "description", "command", "timeout_seconds"})
        require(isinstance(test["id"], str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", test["id"]) and test["id"] not in seen,
                "System smoke test ids must be unique identifiers")
        seen.add(test["id"])
        require(isinstance(test["description"], str) and test["description"].strip(), "System smoke test needs a description")
        require(isinstance(test["command"], list) and test["command"]
                and all(isinstance(arg, str) and arg for arg in test["command"]), "System smoke command must be argv")
        require(type(test["timeout_seconds"]) is int and 1 <= test["timeout_seconds"] <= 3600, "Invalid smoke timeout")
        tests.append({"id": test["id"], "description": test["description"].strip(), "command": list(test["command"]),
                      "timeout_seconds": test["timeout_seconds"]})
    return {"version": 1, "tests": tests}


def _validate_files(files: object) -> list[tuple[str, str]]:
    require(isinstance(files, list) and len(files) <= MAX_FILES, "System smoke files must be a bounded list")
    result = []
    for item in files:
        require(isinstance(item, dict) and set(item) == {"path", "content"}
                and isinstance(item["path"], str) and isinstance(item["content"], str), "Invalid system smoke file")
        relative = repository_relative(item["path"], "system smoke path")
        require(not is_ci_path(relative) and relative != "manifest.json"
                and not LEGACY_CHANGE_DIR.match(relative.split("/", 1)[0]), "Unsafe system smoke path")
        require(len(item["content"].encode("utf-8")) <= MAX_FILE_BYTES, "System smoke file is too large")
        result.append((relative, item["content"]))
    require(len({r for r, _ in result}) == len(result), "System smoke files must have distinct paths")
    return result


def _describe_drift(expected: dict, current: dict) -> str:
    missing = sorted(set(expected) - set(current))
    extra = sorted(set(current) - set(expected))
    changed = sorted(k for k in set(expected) & set(current) if expected[k] != current[k])
    return f"missing={missing} extra={extra} changed={changed}"


def _suite_files(project: Path) -> list[Path]:
    root = project / SYSTEM_SMOKE_DIR
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*")
                  if p.is_file() and not p.is_symlink() and is_system_smoke_path(p.relative_to(project).as_posix()))


def _digests(project: Path) -> dict[str, str]:
    root = project / SYSTEM_SMOKE_DIR
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in _suite_files(project)}


# ---- maintenance: propose (agent) and apply (human) -------------------------------------

DRAFTS = ".storyloop/drafts/smoke"


def adopt_system_smoke(project: Path) -> dict | None:
    """Trust the committed suite of a clean tree (init, base-update): record its digests as protection."""
    project = Path(project).resolve()
    manifest_path = project / SYSTEM_SMOKE_DIR / "manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = validate_manifest(load_json(manifest_path))
    write_json(project / PROTECTION, {"version": 1, "digests": _digests(project)}, max_bytes=2_000_000)
    return manifest


def propose_smoke_update(project: Path, change_id: str, agent=None) -> dict:
    """Ask the acceptance role which core flows of a merged change belong in the system suite.

    Nothing is installed: the proposal lands in `.storyloop/drafts/smoke/<change-id>/` for a human
    to read, edit and apply. The system suite is the whole system's guarantee, so an agent never
    changes it on its own.
    """
    from .changes import load_change
    from .smoke import load_smoke_manifest, suite_root
    from .foundation_survey import foundation_payload

    project = Path(project).resolve()
    change = load_change(project, change_id)
    require(change["status"] == "PASSED", "Smoke maintenance follows a merged (PASSED) change")
    suite = suite_root(project, change_id)
    acceptance_suite = {
        "manifest": load_smoke_manifest(project, change_id),
        "files": [{"path": p.relative_to(suite).as_posix(), "content": p.read_text(encoding="utf-8")}
                  for p in sorted(suite.rglob("*")) if p.is_file() and p.name != "manifest.json"],
    }
    current = load_system_smoke(project)
    system = None if current is None else {
        "manifest": current,
        "files": [{"path": p.relative_to(project / SYSTEM_SMOKE_DIR).as_posix(), "content": p.read_text(encoding="utf-8")}
                  for p in _suite_files(project) if p.name != "manifest.json"],
    }
    context = {"mode": "smoke-maintenance", "change": change, "acceptance_suite": acceptance_suite,
               "system_smoke": system, "smoke_root": SYSTEM_SMOKE_DIR}
    foundation = foundation_payload(project)
    if foundation:
        context["foundation"] = foundation
    if agent is None:
        from .project_agents import invoke_project_agent
        agent = lambda project, context: invoke_project_agent(project, "acceptance", context)  # noqa: E731
    response = agent(project, context)
    require(isinstance(response, dict) and set(response) >= {"files", "manifest"}
            and set(response) <= {"files", "manifest", "rationale"}, "Smoke proposal must be {files, manifest, rationale?}")
    manifest = validate_manifest(response["manifest"])
    sources = _validate_files(response["files"])
    draft = project / DRAFTS / change_id
    if draft.exists():
        for stale in sorted(draft.rglob("*"), reverse=True):
            stale.unlink() if stale.is_file() else stale.rmdir()
    draft.mkdir(parents=True, exist_ok=True)
    for relative, content in sources:
        target = safe_target(draft, relative, "smoke draft path")
        target.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(target, content)
    write_json(draft / "manifest.json", manifest, max_bytes=1_000_000)
    rationale = response.get("rationale") if isinstance(response.get("rationale"), str) else ""
    write_text_atomic(draft / "rationale.md", f"# System smoke update for {change_id}\n\n{rationale.strip()}\n")
    return {"status": "SMOKE_PROPOSED", "change_id": change_id, "draft": str(draft),
            "tests": [t["id"] for t in manifest["tests"]],
            "next": f"review {draft}, edit if needed, then storyloop smoke-apply <project> --change {change_id}"}


def apply_smoke_update(project: Path, change_id: str) -> dict:
    """Install a reviewed draft as the system suite, prove it green, commit; a red draft leaves nothing behind."""
    from .baseline import _git
    from .project_git import commit_paths

    project = Path(project).resolve()
    draft = project / DRAFTS / change_id
    require((draft / "manifest.json").is_file(), f"No smoke draft for {change_id}; run smoke-propose first")
    require(not _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "Git worktree must be clean before applying a system smoke update")
    manifest = load_json(draft / "manifest.json")
    files = [{"path": p.relative_to(draft).as_posix(), "content": p.read_text(encoding="utf-8")}
             for p in sorted(draft.rglob("*")) if p.is_file() and p.name not in {"manifest.json", "rationale.md"}]
    previous_protection = load_json(project / PROTECTION) if (project / PROTECTION).is_file() else None
    install_system_smoke(project, files, manifest)
    result = run_system_smoke_isolated(project)
    changed = [line[3:] for line in _git(project, "status", "--porcelain=v1", "--untracked-files=all").splitlines()]
    if result["status"] != "PASSED":
        _git(project, "checkout", "--", SYSTEM_SMOKE_DIR) if _tracked_under(project, SYSTEM_SMOKE_DIR) else None
        _git(project, "clean", "-fdq", "--", SYSTEM_SMOKE_DIR)
        if previous_protection is None:
            (project / PROTECTION).unlink(missing_ok=True)
        else:
            write_json(project / PROTECTION, previous_protection, max_bytes=2_000_000)
        return {"status": "SMOKE_UPDATE_FAILED", "change_id": change_id, "smoke": result,
                "next": f"fix the draft under {draft} (the suite must be green on main) and smoke-apply again"}
    commit = commit_paths(project, sorted(changed), f"test(smoke): update system smoke for {change_id}")
    for stale in sorted(draft.rglob("*"), reverse=True):
        stale.unlink() if stale.is_file() else stale.rmdir()
    draft.rmdir()
    return {"status": "SMOKE_APPLIED", "change_id": change_id, "commit": commit["commit"],
            "tests": [t["id"] for t in manifest["tests"]]}


def _tracked_under(project: Path, relative: str) -> bool:
    from .baseline import _git
    return bool(_git(project, "ls-files", "-z", "--", relative).strip("\0"))
