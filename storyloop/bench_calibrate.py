"""Calibrate frozen Gold checks without invoking a development agent."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

from .bench_case import load_case
from .harness_command import expand_command
from .models import require
from .processutil import SAFE_ENV, redact, run_process


def calibrate_case(source: Path, repository: Path, output_root: Path,
                   *, python: Path | None = None) -> dict:
    """Check F2P/P2P against disposable snapshots and save an auditable record."""
    case = load_case(source, allow_unapproved_v2=True)
    manifest = case["manifest"]
    interpreter = _python_info(python, manifest["python"])
    repository = Path(repository).resolve()
    require((repository / ".git").exists(), "Repository checkout is unavailable")
    for commit in (manifest["base_commit"], manifest["solution_commit"]):
        found = subprocess.run(["git", "cat-file", "-t", commit], cwd=repository,
                               capture_output=True, text=True, check=False)
        require(found.returncode == 0 and found.stdout.strip() == "commit",
                f"Source commit is unavailable: {commit}")
    result = {"case_id": manifest["case_id"], "case_version": manifest["case_version"],
              "case_hash": case["case_hash"], "repository_id": manifest["repository_id"],
              "base_commit": manifest["base_commit"], "solution_commit": manifest["solution_commit"],
              "contract_sha256": manifest["contract_sha256"], "suite_sha256": manifest["suite_sha256"],
              "gold_sha256": manifest["gold_sha256"], "python": interpreter,
              "contract_approved": case["contract_approved"],
              "approval_stale": case["approval_stale"],
              "status": "FAILED", "reasons": ["approval_stale"] if case["approval_stale"] else [],
              "checks": []}
    with tempfile.TemporaryDirectory(prefix="storyloop-bench-calibrate-") as folder:
        root = Path(folder)
        for side, commit in (("base", manifest["base_commit"]),
                             ("solution", manifest["solution_commit"])):
            _snapshot(repository, commit, root / side)
            _overlay(case, root / side)
        for check in case["gold"]["checks"]:
            row = {"check_id": check["check_id"], "kind": check["kind"],
                   "argv": check["argv"], "timeout_seconds": check["timeout_seconds"],
                   "check_sha256": hashlib.sha256(json.dumps(check, sort_keys=True).encode()).hexdigest()}
            for side in ("base", "solution"):
                destination = root / f"attempt-{len(result['checks'])}-{side}"
                shutil.copytree(root / side, destination, symlinks=False)
                row[side] = _execute(check, destination, interpreter["path"])
            result["checks"].append(row)
            expected = ((False, True) if check["kind"] == "F2P" else (True, True))
            observed = tuple(row[side]["passed"] for side in ("base", "solution"))
            for side in ("base", "solution"):
                if row[side]["error"] or row[side]["timed_out"] or not row[side]["contained"]:
                    detail = row[side]["failure_evidence"] or row[side]["error"] or "timeout/containment"
                    result["reasons"].append(f"{check['check_id']} {side}: {detail}")
            if observed != expected:
                result["reasons"].append(f"{check['check_id']}: expected {expected}, got {observed}")
    if not result["reasons"]:
        result["status"] = "CALIBRATED"
    result["calibrated_at"] = datetime.now(timezone.utc).isoformat()
    records = Path(output_root) / ".storyloop/bench/calibrations"
    records.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    record = records / f"{manifest['case_id']}-v{manifest['case_version']}-{stamp}.json"
    result["record_path"] = str(record)
    record.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def _snapshot(repository: Path, commit: str, destination: Path) -> None:
    destination.mkdir(parents=True)
    with tempfile.TemporaryFile() as archive:
        completed = subprocess.run(["git", "archive", "--format=tar", commit], cwd=repository,
                                   stdout=archive, stderr=subprocess.PIPE, check=False)
        require(completed.returncode == 0, "Unable to archive source commit")
        archive.seek(0)
        with tarfile.open(fileobj=archive, mode="r:") as source:
            for member in source:
                relative = PurePosixPath(member.name)
                require(not relative.is_absolute() and ".." not in relative.parts,
                        "Source archive contains an unsafe path")
                require(member.isdir() or member.isfile(),
                        "Source archive contains a link or special file")
                target = destination.joinpath(*relative.parts)
                require(target.resolve().is_relative_to(destination), "Source archive escapes snapshot")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source.extractfile(member) as data, target.open("wb") as output:
                        shutil.copyfileobj(data, output)


def _overlay(case: dict, destination: Path) -> None:
    for item in case["manifest"]["suite_files"]:
        relative = PurePosixPath(item["path"])
        target = destination.joinpath(*relative.parts[1:])
        require(target.resolve().is_relative_to(destination), "Suite overlay escapes snapshot")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((case["source"] / item["path"]).read_bytes())


def _execute(check: dict, destination: Path, python_path: str) -> dict:
    argv = [arg.removeprefix("checks/") if arg.startswith("checks/") else arg
            for arg in check["argv"]]
    argv = ([python_path, *argv[1:]] if argv[0] == "{python}"
            else [python_path, *expand_command(argv)[1:]])
    started = time.monotonic()
    outcome = run_process(argv, destination, check["timeout_seconds"],
                          environment_allowlist=SAFE_ENV | {"PYTHONPATH"},
                          source_environment={**os.environ, "PYTHONPATH": str(destination)})
    return {"command": argv, "returncode": outcome["returncode"],
            "duration_ms": round((time.monotonic() - started) * 1000),
            "timed_out": outcome["timed_out"], "contained": outcome["contained"],
            "stdout_sha256": hashlib.sha256(outcome["stdout"].encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(outcome["stderr"].encode()).hexdigest(),
            "failure_evidence": redact(outcome["stderr"][-800:]) if outcome["returncode"] else "",
            "error": outcome["error"], "passed": outcome["returncode"] == 0 and
            not outcome["timed_out"] and outcome["contained"] and not outcome["error"]}


def _python_info(python: Path | None, requirement: dict) -> dict:
    selected = Path(python or sys.executable).absolute()
    require(selected.is_file() and os.access(selected, os.X_OK), "Gold Python interpreter is unavailable")
    probe = subprocess.run([str(selected), "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
                           capture_output=True, text=True, timeout=10, check=False)
    require(probe.returncode == 0, "Unable to inspect Gold Python interpreter")
    version = probe.stdout.strip()
    require(".".join(version.split(".")[:2]) == requirement["version"],
            f"Gold requires Python {requirement['version']}; selected interpreter is {version}")
    return {"path": str(selected), "version": version, "requirement": requirement}


def compare_calibrations(first: dict, second: dict) -> dict:
    """Calibration records from distinct interpreters or Case bytes cannot be compared."""
    reasons = [field for field in ("case_hash", "python") if first.get(field) != second.get(field)]
    return {"status": "NOT_COMPARABLE" if reasons else "COMPARABLE", "reasons": reasons}


def judge_gold(source: Path, tree: Path, *, python: Path | None = None) -> dict:
    """Execute hidden Gold in isolated copies of the candidate tree."""
    case = load_case(source)
    interpreter = _python_info(python, case["manifest"]["python"])
    checks = []
    with tempfile.TemporaryDirectory(prefix="storyloop-bench-gold-") as folder:
        for index, check in enumerate(case["gold"]["checks"]):
            destination = Path(folder) / f"check-{index}"
            shutil.copytree(tree, destination, ignore=shutil.ignore_patterns(".git"))
            _overlay(case, destination)
            checks.append({"check_id": check["check_id"],
                           **_execute(check, destination, interpreter["path"])})
    return {"passed": all(row["passed"] for row in checks), "checks": checks,
            "case_hash": case["case_hash"], "python": interpreter}
