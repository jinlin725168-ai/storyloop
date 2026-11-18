"""Roadmap H: bring a worker's verified result back to the project.

If the project branch has not moved since the worker was cloned, the worker's commits land by
fast-forward: exactly what was verified. If another change landed meanwhile, the worker's branch
is merged in a fresh integration clone and re-verified there (the change's acceptance suite,
system smoke and the project's unit and integration commands) before the project fast-forwards
to that merge. A textual conflict or a red check stops for a person; nothing is forced.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile

from .baseline import _git, advance_repository_head, load_baseline, load_project_config
from .models import StoryLoopError, require
from .storage import load_json
from .worker_registry import _alive, _copy_control_plane, _entries, _worker_status, save_entry

# Control-plane files of one change, copied back so status, log, metrics and reports see it.
PER_CHANGE = ("state", "evidence", "drafts")


def land_worker(project: Path, change_id: str) -> dict:
    project = Path(project).resolve()
    entry = _entries(project).get(change_id)
    require(entry is not None, f"No worker for {change_id}")
    require(entry["state"] != "landed", f"{change_id} has already landed")
    target = Path(entry["path"])
    require(not (entry["state"] == "running" and _alive(entry.get("pid"))), "The worker is still running")
    status = _worker_status(target, change_id)
    require(status == "PASSED", f"The worker's run is {status}, not PASSED")
    require(not _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "Git worktree must be clean before landing")
    branch = load_baseline(project)["source_branch"]
    require(_git(project, "branch", "--show-current").strip() == branch, f"Land on {branch}")
    head = _git(project, "rev-parse", "HEAD").strip()
    worker_head = _git(target, "rev-parse", "HEAD").strip()
    if head == entry["base_head"]:
        _fast_forward(project, target, worker_head)
        verification = "exact"
    else:
        merged = _reverify(project, target, change_id, worker_head)
        if merged["status"] != "PASSED":
            return {**merged, "change_id": change_id, "worker": str(target)}
        _fast_forward(project, Path(merged["clone"]), merged["head"])
        shutil.rmtree(merged["clone"], ignore_errors=True)
        verification = "reverified"
    landed = _git(project, "rev-parse", "HEAD").strip()
    _copy_back(project, target, change_id)
    advance_repository_head(project, landed)
    save_entry(project, {**entry, "state": "landed", "landed_head": landed, "verification": verification})
    return {"status": "LANDED", "change_id": change_id, "landed_head": landed, "verification": verification,
            "report": str(project / "reports/storyloop" / change_id / "report.md")}


def _fast_forward(project: Path, source: Path, commit: str) -> None:
    subprocess.run(["git", "-C", str(project), "fetch", "-q", str(source), commit], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(project), "merge", "-q", "--ff-only", commit], check=True, capture_output=True)


def _reverify(project: Path, target: Path, change_id: str, worker_head: str) -> dict:
    """Merge the worker into the moved branch in a scratch clone and re-run the checks there."""
    clone = Path(tempfile.mkdtemp(prefix=f"storyloop-land-{change_id}-"))
    shutil.rmtree(clone)
    branch = load_baseline(project)["source_branch"]
    subprocess.run(["git", "clone", "-q", "--no-local", "--branch", branch, str(project), str(clone)],
                   check=True, capture_output=True)
    _copy_control_plane(project / ".storyloop", clone / ".storyloop")
    _copy_back(clone, target, change_id, touches=False)
    subprocess.run(["git", "-C", str(clone), "fetch", "-q", str(target), worker_head], check=True, capture_output=True)
    identity = []
    for key, fallback in (("user.name", "StoryLoop"), ("user.email", "storyloop@localhost")):
        value = subprocess.run(["git", "-C", str(project), "config", "--get", key],
                               capture_output=True, text=True).stdout.strip()
        identity += ["-c", f"{key}={value or fallback}"]
    merge = subprocess.run(["git", *identity, "-C", str(clone), "merge", "--no-ff", "-m",
                            f"Merge {change_id} (storyloop workers land, re-verified on the moved branch)", worker_head],
                           capture_output=True, text=True)
    if merge.returncode != 0:
        conflicts = subprocess.run(["git", "-C", str(clone), "diff", "--name-only", "--diff-filter=U"],
                                   capture_output=True, text=True).stdout.split()
        shutil.rmtree(clone, ignore_errors=True)
        return {"status": "LAND_CONFLICT", "conflicts": conflicts,
                "next": f"另一个变更已先落地且改了同样的文件；在项目里手工合并 worker 分支，或基于新 main 重做 {change_id}"}
    head = _git(clone, "rev-parse", "HEAD").strip()
    checks = _checks(clone, change_id, head)
    if checks["failed"]:
        shutil.rmtree(clone, ignore_errors=True)
        return {"status": "LAND_REVERIFY_FAILED", "failed": checks["failed"],
                "next": f"合并后复验未通过（{', '.join(checks['failed'])}）：基于新 main 重做 {change_id}，或人工修复后再 land"}
    return {"status": "PASSED", "clone": str(clone), "head": head}


def _checks(clone: Path, change_id: str, head: str) -> dict:
    from .ci_command import run_ci
    from .project_profile import layer_commands
    from .smoke_runner import run_commands_at_commit
    config = load_project_config(clone)
    failed = []
    for layer in ("unit", "integration"):
        commands = layer_commands(config["profile"], layer)
        if commands and run_commands_at_commit(clone, head, commands, config["check_timeout_seconds"])["status"] != "PASSED":
            failed.append(layer)
    try:
        ci = run_ci(clone, change_id)
    except StoryLoopError:  # an unreadable or missing suite is a failed check, not a crash
        return {"failed": [*failed, "acceptance"]}
    if ci["acceptance"]["status"] != "PASSED":
        failed.append("acceptance")
    if ci["smoke"]["status"] not in {"PASSED", "SKIPPED"}:
        failed.append("smoke")
    return {"failed": failed}


def _copy_back(project: Path, target: Path, change_id: str, *, touches: bool = True) -> None:
    """Copy the change's own control-plane files (and call records) from the worker."""
    source = target / ".storyloop"
    for top in PER_CHANGE:
        for path in (source / top).rglob("*") if (source / top).is_dir() else []:
            relative = path.relative_to(source)
            if not path.is_file() or path.is_symlink() or change_id not in relative.as_posix():
                continue
            if relative.parts[:2] == ("state", "touches"):
                continue
            destination = project / ".storyloop" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
    _copy_calls(source, project / ".storyloop", change_id)
    if touches:
        ledger = source / "state/touches" / f"{change_id}.jsonl"
        if ledger.is_file():
            mine = project / ".storyloop/state/touches" / f"{change_id}.jsonl"
            mine.parent.mkdir(parents=True, exist_ok=True)
            with mine.open("a", encoding="utf-8") as handle:
                handle.write(ledger.read_text(encoding="utf-8"))


def _copy_calls(source: Path, destination: Path, change_id: str) -> None:
    ledger = source / "state/telemetry" / f"{change_id}.json"
    try:
        entries = load_json(ledger).get("entries") or [] if ledger.is_file() else []
    except (OSError, ValueError, StoryLoopError):
        return
    for entry in entries:
        call = entry.get("call") if isinstance(entry, dict) else None
        folder = source / "state/telemetry/calls" / str(call)
        if call and folder.is_dir() and "/" not in str(call) and ".." not in str(call):
            shutil.copytree(folder, destination / "state/telemetry/calls" / str(call), dirs_exist_ok=True)

