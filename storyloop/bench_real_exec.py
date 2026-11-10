"""Disposable source and engine checkouts for one frozen bench attempt."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Callable

from .bench_calibrate import judge_gold
from .models import StoryLoopError, require
from .processutil import run_process

COMMIT = re.compile(r"[0-9a-f]{40}\Z")
STAGE_CASE_IDS = frozenset({"dogfood3-stash-git-safe", "dogfood5-stash-hint-coverage",
                            "dogfood6-design-revision-findings", "dogfood9-scratch-glob",
                            "dogfood10-repair-next-path"})


def run_isolated_attempt(case: dict, repository: Path, engine_commit: str,
                         adapter: str, model: str, budget: dict, human: Callable,
                         *, engine: Callable | None = None,
                         engine_repository: Path | None = None,
                         engine_python: Path | None = None,
                         gold_python: Path | None = None,
                         agent_command: list[str] | None = None,
                         suite_source: str = "generated",
                         evidence_root: Path | None = None) -> dict:
    """Clone sources per attempt; execute and judge before both trees are discarded."""
    require(COMMIT.fullmatch(engine_commit) is not None, "Invalid engine commit")
    require(suite_source in {"generated", "frozen"}, "Invalid bench suite source")
    source = Path(repository).resolve()
    require(source.is_dir() and (source / ".git").exists(), "Source repository is unavailable")
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="storyloop-bench-attempt-") as folder:
        root = Path(folder)
        tree = root / "candidate"
        _checkout_source(source, case["manifest"]["base_commit"], tree)
        if engine is None:
            require(adapter in {"codex", "claude"}, "Real bench adapter is unavailable")
            require(engine_repository is not None, "Real bench needs --engine-repository")
            engine_tree = root / "engine"
            _checkout(Path(engine_repository).resolve(), engine_commit, engine_tree)
            outcome = _worker(tree, engine_tree, case, adapter, model, budget,
                              Path(engine_python or sys.executable), agent_command, suite_source)
        else:
            outcome = engine(tree, case, human, budget)
        require(isinstance(outcome, dict), "Bench engine returned no outcome")
        outcome["engine_commit"] = engine_commit
        outcome["suite_source"] = suite_source
        outcome["wall_ms"] = round((time.monotonic() - started) * 1000)
        if evidence_root is not None and outcome.get("readiness_evidence_source"):
            _preserve_readiness(tree, outcome, Path(evidence_root))
        if outcome.get("status") == "PASSED":
            _safe_gold_tree(tree)
            gold = judge_gold(case["source"], tree, python=gold_python)
            outcome["gold_passed"] = gold["passed"]
            outcome["gold_checks"] = gold["checks"]
        return outcome


def _safe_gold_tree(tree: Path) -> None:
    """Reject links and special files before Gold copies a candidate tree."""
    for current, directories, files in os.walk(tree, topdown=True, followlinks=False):
        if current == str(tree):
            directories[:] = [name for name in directories if name != ".git"]
        for name in (*directories, *files):
            path = Path(current) / name
            mode = path.lstat().st_mode
            require(stat.S_ISREG(mode) or stat.S_ISDIR(mode),
                    f"Gold candidate contains a link or special file: {path.relative_to(tree)}")


def _checkout(repository: Path, commit: str, destination: Path) -> None:
    require(COMMIT.fullmatch(commit) is not None, "Invalid checkout commit")
    found = subprocess.run(["git", "cat-file", "-t", commit], cwd=repository,
                           capture_output=True, text=True, check=False)
    require(found.returncode == 0 and found.stdout.strip() == "commit", "Commit is unavailable")
    cloned = subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", str(repository), str(destination)],
                            capture_output=True, text=True, check=False)
    require(cloned.returncode == 0, "Unable to clone bench repository")
    checked = subprocess.run(["git", "checkout", "--quiet", "--detach", commit], cwd=destination,
                             capture_output=True, text=True, check=False)
    require(checked.returncode == 0, "Unable to checkout frozen commit")
    branched = subprocess.run(["git", "switch", "--quiet", "-c", "storyloop-bench-attempt"],
                              cwd=destination, capture_output=True, text=True, check=False)
    require(branched.returncode == 0, "Unable to create disposable attempt branch")


def _checkout_source(repository: Path, commit: str, destination: Path) -> None:
    """Fetch only Base so the candidate cannot inspect Solution through Git history."""
    require(COMMIT.fullmatch(commit) is not None, "Invalid source commit")
    destination.mkdir()
    commands = (["git", "init", "--quiet"],
                ["git", "-c", "protocol.file.allow=always", "fetch", "--quiet", "--depth", "1",
                 "--no-tags", repository.as_uri(), commit],
                ["git", "checkout", "--quiet", "-b", "storyloop-bench-attempt", "FETCH_HEAD"],
                ["git", "config", "user.name", "StoryLoop Bench"],
                ["git", "config", "user.email", "bench@example.invalid"])
    for command in commands:
        result = subprocess.run(command, cwd=destination, capture_output=True, text=True, check=False)
        require(result.returncode == 0, "Unable to prepare isolated Base repository")


def _worker(tree: Path, engine_tree: Path, case: dict, adapter: str, model: str,
            budget: dict, python: Path, agent_command: list[str] | None,
            suite_source: str) -> dict:
    require(python.is_file(), "Engine Python interpreter is unavailable")
    venv = subprocess.run([str(python), "-I", "-B", "-c",
                           "import sys; print(sys.prefix != sys.base_prefix)"],
                          capture_output=True, text=True, timeout=10, check=False)
    require(venv.returncode == 0 and venv.stdout.strip() == "True",
            "Engine Python must come from an isolated virtual environment")
    attempt_python, engine_sites = _attempt_python(python, tree)
    config = {"project": str(tree), "engine_tree": str(engine_tree),
              "contract": case["contract"], "case_source": str(case["source"]),
              "adapter": adapter, "model": model,
              "budget": budget, "agent_command": agent_command,
              "engine_sites": engine_sites,
              "case_version": case["manifest"]["case_version"],
              "suite_source": suite_source}
    request = tree.parent / "request.json"
    response = tree.parent / "response.json"
    request.write_text(json.dumps(config, ensure_ascii=False))
    script = Path(__file__).with_name("bench_worker.py")
    limit = budget.get("max_wall_seconds", 14_400)
    require(type(limit) is int and limit > 0, "Invalid wall time budget")
    environment = {**os.environ, "PATH": str(attempt_python.parent) + os.pathsep
                   + os.environ.get("PATH", "")}
    completed = run_process([str(attempt_python), "-I", "-S", "-B", str(script),
                             str(request), str(response)], tree, limit,
                            source_environment=environment)
    require(completed["contained"], "Bench worker process tree was not contained")
    if completed["timed_out"]:
        return {"status": "TIMEOUT", "termination_reason": "max_wall_seconds", "events": [],
                "calls": None, "agent_ms": None, "known_cost_usd": None, "cost_coverage": None}
    if not response.is_file():
        raise StoryLoopError(f"Bench engine worker failed (exit {completed['returncode']})")
    outcome = json.loads(response.read_text())
    require(isinstance(outcome, dict), "Invalid bench worker response")
    outcome["engine_python"] = str(python.absolute())
    outcome["attempt_python"] = str(attempt_python.absolute())
    return outcome


def _attempt_python(engine_python: Path, tree: Path) -> tuple[Path, list[str]]:
    """Give project checks their own importable source with engine venv tools."""
    root = tree.parent / "venv"
    subprocess.run([str(engine_python), "-m", "venv", "--without-pip", str(root)],
                   check=True, capture_output=True, text=True)
    candidate = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    query = "import json,sysconfig; print(json.dumps([sysconfig.get_path('purelib'), sysconfig.get_path('platlib')]))"
    engine_paths = json.loads(subprocess.check_output([str(engine_python), "-I", "-B", "-c", query], text=True))
    attempt_paths = json.loads(subprocess.check_output([str(candidate), "-I", "-B", "-c", query], text=True))
    for site in set(attempt_paths):
        Path(site).joinpath("storyloop-bench-attempt.pth").write_text(
            "\n".join([*dict.fromkeys(engine_paths),
                       f"import sys; sys.path.insert(0, {str(tree)!r})"]) + "\n")
    for tool in engine_python.parent.iterdir():
        if not tool.is_file() or tool.name.lower().startswith(("python", "activate", "pip")):
            continue
        target = candidate.parent / tool.name
        if target.exists():
            continue
        if os.name == "nt":
            shutil.copy2(tool, target)
        else:
            target.symlink_to(tool)
    return candidate, list(dict.fromkeys(engine_paths))


def _preserve_readiness(tree: Path, outcome: dict, root: Path) -> None:
    source = Path(outcome.pop("readiness_evidence_source"))
    require(not source.is_absolute() and source.parts[:3] == (".storyloop", "evidence", "readiness")
            and ".." not in source.parts, "Invalid readiness evidence path")
    original = tree / source
    require(original.resolve().is_relative_to(tree.resolve())
            and not any(path.is_symlink() for path in (original, *original.parents) if path != tree.parent)
            and original.is_file() and original.stat().st_size <= 2_000_000,
            "Readiness evidence is unavailable")
    from .processutil import redact
    def clean(value):
        if isinstance(value, str):
            return redact(value)
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        return value
    relative = Path(".storyloop/bench/evidence") / uuid.uuid4().hex / "readiness.json"
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=False)
    target.write_text(json.dumps(clean(json.loads(original.read_text())), ensure_ascii=False))
    outcome["readiness_evidence_path"] = relative.as_posix()


def stage_gate(run: dict) -> dict:
    """Absolute v2 base-only gate: four of five complete Cases pass twice."""
    require(run.get("case_version") == 2 and run.get("scope") == "base-only"
            and run.get("suite_source") == "generated",
            "Stage gate requires a generated-suite v2 base-only Run")
    cases = run.get("cases")
    require(isinstance(cases, list) and len(cases) == 5
            and {row.get("case_id") for row in cases} == STAGE_CASE_IDS
            and all(row.get("case_version") == 2 for row in cases),
            "Stage gate requires the five frozen v2 Cases")
    passed = [row["case_id"] for row in cases if row.get("comparison", {}).get("status") == "BASE_ONLY"
              and len(row.get("attempts", [])) == 2 and all(
        item.get("verdict") == "PASSED" and type(item.get("nonroutine_count")) is int
        and item["nonroutine_count"] <= 1 for item in row["attempts"])]
    return {"passed": len(passed) >= 4, "passed_cases": passed, "required": 4,
            "total": 5}
