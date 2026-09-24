"""CI-neutral execution of protected smoke manifests."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

from .baseline import _git, load_project_config
from .boundaries import capture_snapshot, snapshot_changes
from .models import StoryLoopError, require
from .path_security import safe_target
from .project_profile import GENERATED_PATHS
from .processutil import redact, run_process
from .smoke import (RED_EVIDENCE, RESULT_EVIDENCE, load_smoke_manifest, protection_path, smoke_digests,
                    suite_relative, suite_root)
from .storage import load_json, write_json
from .system_smoke import run_system_smoke_isolated


def run_smoke(
    project: Path, change_id: str, expect_red: bool = False,
    head_commit: str | None = None,
    workspace_sha256: str | None = None,
) -> dict:
    """Execute exactly the protected manifest and persist bounded evidence."""
    project = Path(project).resolve()
    manifest = load_smoke_manifest(project, change_id)
    started = _timestamp()
    results = [_run_test(project, item) for item in manifest["tests"]]
    errors = [item for item in results if item["status"] == "ERROR"]
    failures = [item for item in results if item["status"] == "FAIL"]
    if expect_red:
        require(not errors, "Smoke RED proof encountered an environment error")
        if not failures:
            raise StoryLoopError("Smoke suite is already green and did not prove RED", code="EXISTING_GREEN")
        criteria = {criterion for item in manifest["tests"] for criterion in item["criteria"]}
        failed_criteria = {criterion for item in failures for criterion in item["criteria"]}
        require(criteria <= failed_criteria,
                "RED proof must fail at least one test for every automated criterion")
        status = "RED"
    elif errors:
        status = "ERROR"
    elif failures:
        status = "FAILED"
    else:
        status = "PASSED"
    suite = smoke_digests(project, change_id)
    _require_same_red_suite(project, change_id, suite, expect_red)
    evidence = {
        "version": 1,
        "change_id": change_id,
        "status": status,
        "expect_red": expect_red,
        "started_at": started,
        "finished_at": _timestamp(),
        "head_commit": head_commit or _git(project, "rev-parse", "HEAD").strip(),
        "workspace_sha256": workspace_sha256 or candidate_digest(project),
        "suite_digests": suite,
        "results": results,
    }
    path = project / ".storyloop" / "evidence" / change_id / (
        RED_EVIDENCE if expect_red else RESULT_EVIDENCE
    )
    write_json(path, evidence, max_bytes=4_000_000)
    return {**evidence, "evidence_path": str(path), "evidence_sha256": _sha256(path)}


def run_smoke_isolated(project: Path, change_id: str, expect_red: bool = False,
                       at_commit: str | None = None) -> dict:
    """Run smoke from a clean candidate copy that excludes every Git-ignored file.

    With `at_commit`, the candidate is the tree at that commit plus the current smoke suite.
    """
    project = Path(project).resolve()
    head = at_commit or _git(project, "rev-parse", "HEAD").strip()
    with tempfile.TemporaryDirectory(prefix="storyloop-smoke-") as folder:
        candidate = Path(folder)
        paths = (_copy_candidate_from_commit(project, candidate, at_commit, change_id) if at_commit
                 else _copy_candidate(project, candidate))
        _copy_smoke_control(project, candidate, change_id)
        before = capture_snapshot(candidate, include_control=True, include_ignored=True)
        digest = _files_digest(candidate, paths)
        result = run_smoke(
            candidate, change_id, expect_red, head_commit=head,
            workspace_sha256=digest,
        )
        source = Path(result["evidence_path"])
        after = capture_snapshot(candidate, include_control=True, include_ignored=True)
        allowed = f".storyloop/evidence/{change_id}/{source.name}"
        _require_only_evidence_changed(before, after, allowed)
        target = project / ".storyloop/evidence" / change_id / source.name
        write_json(target, load_json(source), max_bytes=4_000_000)
    return {**load_json(target), "evidence_path": str(target),
            "evidence_sha256": _sha256(target)}


def _copy_candidate(project: Path, candidate: Path) -> list[str]:
    output = _git(project, "ls-files", "-co", "--exclude-standard", "-z")
    paths = sorted(path for path in set(filter(None, output.split("\0"))) if not _is_regression_asset(path))
    allowed = _candidate_allow(project)
    for relative in paths:
        source = safe_target(project, relative, "candidate source")
        require(source.is_file(), "Candidate contains an unsupported entry")
        name = source.name.casefold()
        require(relative in allowed or (
                not name.startswith(".env") and not name.endswith((".key", ".pem"))
                and name not in {"credentials.json", "secrets.yaml"}),
                "Sensitive files cannot enter an acceptance candidate")
        target = safe_target(candidate, relative, "candidate target")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        shutil.copymode(source, target)
    return paths


UI_ASSETS = "acceptance/ui/"


def _is_regression_asset(path: str) -> bool:
    """UI regression assets document current screens; they are never acceptance evidence."""
    return path.startswith(UI_ASSETS)


def _copy_candidate_from_commit(project: Path, candidate: Path, commit: str, change_id: str) -> list[str]:
    """Rebuild the tree as it was at `commit`, overlaid with the change's current smoke suite.

    Used to re-prove RED after a suite repair: the implementation may already exist in the
    working tree, but at the contract commit it does not, so a correct suite must fail there.
    """
    archive = subprocess.run(["git", "archive", "--format=tar", commit], cwd=project,
                             capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        members = [m for m in tar.getmembers() if m.isfile() and not _is_regression_asset(m.name)]
        for member in members:
            safe_target(candidate, member.name, "candidate target")
        tar.extractall(candidate, members=members, filter="data")
    paths = {member.name for member in members}
    suite = suite_root(project, change_id)
    for stale in sorted((candidate / suite_relative(project, change_id)).rglob("*"), reverse=True):
        if stale.is_file():
            stale.unlink()
    paths = {path for path in paths if not path.startswith(f"acceptance/smoke/{change_id}/")}
    for source in sorted(path for path in suite.rglob("*") if path.is_file()):
        relative = source.relative_to(project).as_posix()
        target = safe_target(candidate, relative, "candidate target")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        paths.add(relative)
    return sorted(paths)


def _candidate_allow(project: Path) -> set[str]:
    """Tracked paths the project explicitly cleared despite sensitive-looking names."""
    profile = load_project_config(project).get("profile", {})
    values = profile.get("candidate_allow", []) if isinstance(profile, dict) else []
    require(isinstance(values, list) and all(isinstance(item, str) for item in values),
            "candidate_allow must be a list of relative paths")
    return set(values)


def _copy_smoke_control(project: Path, candidate: Path, change_id: str) -> None:
    """Copy the control files a candidate needs, including config for snapshot allow lists."""
    project = Path(project).resolve()  # protection_path() resolves too; Windows 8.3 names must not differ
    pairs = [
        (project / ".storyloop/config.json", candidate / ".storyloop/config.json"),
        (project / ".storyloop/state/changes" / f"{change_id}.json",
         candidate / ".storyloop/state/changes" / f"{change_id}.json"),
        (protection_path(project, change_id),
         candidate / protection_path(project, change_id).relative_to(project)),
    ]
    red = project / ".storyloop/evidence" / change_id / RED_EVIDENCE
    if red.exists():
        pairs.append((red, candidate / ".storyloop/evidence" / change_id / red.name))
    for source, target in pairs:
        write_json(target, load_json(source), max_bytes=4_000_000)


def _require_only_evidence_changed(before, after, allowed: str) -> None:
    changes = snapshot_changes(before, after)
    unexpected = [
        item for item in changes
        if item.path != allowed
        and not (item.path.endswith("/") and allowed.startswith(item.path))
    ]
    if unexpected:
        paths = ", ".join(item.path for item in unexpected[:20])
        raise StoryLoopError(
            f"Smoke test modified its isolated candidate: {paths}",
            code="WORKSPACE_TAINTED",
        )


def run_commands(project: Path, commands: list[list[str]], timeout_seconds: int) -> dict:
    """Run generic project regression commands with the same safe process contract."""
    require(isinstance(commands, list), "Regression commands must be a list")
    if not commands:
        return {"status": "SKIPPED", "reason": "NO_REGRESSION_COMMANDS", "results": []}
    tests = [
        {"id": f"REGRESSION-{index:03d}", "criteria": [], "command": command,
         "timeout_seconds": timeout_seconds}
        for index, command in enumerate(commands, start=1)
    ]
    results = [_run_test(Path(project).resolve(), item) for item in tests]
    status = "PASSED" if all(item["status"] == "PASS" for item in results) else "FAILED"
    return {"status": status, "results": results}


def run_commands_isolated(
    project: Path, commands: list[list[str]], timeout_seconds: int,
) -> dict:
    """Run regression in a disposable Git-visible candidate and bind its input."""
    project = Path(project).resolve()
    before = capture_snapshot(project, include_control=True, include_ignored=True)
    try:
        with tempfile.TemporaryDirectory(prefix="storyloop-regression-") as folder:
            candidate = Path(folder)
            paths = _copy_candidate(project, candidate)
            candidate_digest = _files_digest(candidate, paths)
            result = run_commands(candidate, commands, timeout_seconds)
    finally:
        after = capture_snapshot(project, include_control=True, include_ignored=True)
        if snapshot_changes(before, after):
            raise StoryLoopError("Regression modified the source project",
                                 code="WORKSPACE_TAINTED")
    return {**result, "candidate_sha256": candidate_digest}


def git_visible_digest(project: Path) -> str:
    """Bind every tracked or unignored file, regardless of directory name (whole working tree)."""
    project = Path(project).resolve()
    output = _git(project, "ls-files", "-co", "--exclude-standard", "-z")
    return _files_digest(project, sorted(set(filter(None, output.split("\0")))))


def implementation_digest(project: Path) -> str:
    """What a human accepts: Git-visible files minus engine outputs (openspec, ui assets, reports)."""
    project = Path(project).resolve()
    output = _git(project, "ls-files", "-co", "--exclude-standard", "-z")
    paths = sorted(path for path in set(filter(None, output.split("\0")))
                   if not any(path.startswith(root + "/") for root in GENERATED_PATHS))
    return _files_digest(project, paths)


def candidate_digest(project: Path) -> str:
    """The digest an acceptance candidate of this tree would carry (regression assets excluded)."""
    project = Path(project).resolve()
    output = _git(project, "ls-files", "-co", "--exclude-standard", "-z")
    paths = sorted(path for path in set(filter(None, output.split("\0"))) if not _is_regression_asset(path))
    return _files_digest(project, paths)


def _files_digest(root: Path, paths: list[str]) -> str:
    digest = hashlib.sha256()
    for relative in paths:
        path = safe_target(root, relative, "candidate digest path")
        require(path.is_file(), "Candidate digest input is unavailable")
        digest.update(relative.replace("\\", "/").encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _run_test(project: Path, test: dict) -> dict:
    argv = [_expand(item) for item in test["command"]]
    executable = argv[0] if Path(argv[0]).is_absolute() else shutil.which(argv[0])
    require(bool(executable), "Smoke executable is unavailable")
    require(Path(executable).suffix.lower() not in {".cmd", ".bat", ".ps1"},
            "Smoke executable must be native or use an explicit interpreter")
    argv[0] = str(executable)
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    outcome = run_process(
        argv,
        project,
        test["timeout_seconds"],
        environment_allowlist=["PATH", "SYSTEMROOT", "TEMP", "TMP",
                               "PYTHONDONTWRITEBYTECODE"],
        source_environment=environment,
    )
    if (outcome.get("contained") is not True or outcome.get("timed_out")
            or outcome.get("truncated") or outcome.get("error")):
        status = "ERROR"
    elif outcome.get("returncode") == 0:
        status = "PASS"
    elif outcome.get("returncode") == 1:
        status = "FAIL"
    else:
        status = "ERROR"
    return {
        "id": test["id"],
        "criteria": list(test["criteria"]),
        "command": argv,
        "status": status,
        "returncode": outcome.get("returncode"),
        "timed_out": bool(outcome.get("timed_out")),
        "stdout": redact(str(outcome.get("stdout", "")))[:100_000],
        "stderr": redact(str(outcome.get("stderr", "")))[:100_000],
    }


def _expand(value: str) -> str:
    return sys.executable if value == "{python}" else value


def _require_same_red_suite(
    project: Path, change_id: str, suite: dict, expect_red: bool,
) -> None:
    red_path = project / ".storyloop" / "evidence" / change_id / RED_EVIDENCE
    if expect_red or not red_path.exists():
        return
    red = load_json(red_path)
    require(red.get("change_id") == change_id and red.get("status") == "RED",
            "Invalid RED evidence")
    require(red.get("suite_digests") == suite,
            "Green smoke suite differs from the RED-proven suite")


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_ci(project: Path, change_id: str) -> dict:
    """The CI entry: the change's acceptance suite plus the system smoke suite, both isolated."""
    acceptance = run_smoke_isolated(project, change_id)
    smoke = run_system_smoke_isolated(project)
    status = "PASSED" if acceptance["status"] == "PASSED" and smoke["status"] in {"PASSED", "SKIPPED"} else "FAILED"
    return {"status": status, "change_id": change_id, "acceptance": acceptance, "smoke": smoke}
