"""Deterministic, bounded audit projections of authoritative v2 state."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from .artifact_chain import validate_artifact_chain
from .final_judges import safe_final_evidence
from .json_contract import dumps_strict_json, loads_strict_json
from .models import StoryLoopError, fields, identifier, require
from .stage_agents import adapter_control_root
from .stage_config import stage_order
from .stage_result import safe_result
from .storage import write_json

ATTEMPT_PROTOCOL = "loop.run.attempt.v2"
FINAL_PROTOCOL = "loop.run.final.v2"
MAX_ARTIFACT_BYTES = 1024 * 1024


def run_root(project: Path) -> Path:
    """Return the external, non-Agent-writable audit root."""
    return adapter_control_root(project) / "runs"


def record_stage_attempt(project: Path, config: dict, state: dict,
                         adapter_id: str) -> str:
    """Create or verify one audit projection and return its canonical digest."""
    validate_artifact_chain(state)
    stage = identifier(state.get("stage"), "stage id")
    require(adapter_id in config["agent"]["adapters"], "Unknown audit adapter")
    attempt = state.get("attempts_started")
    require(type(attempt) is int and attempt > 0, "Attempt artifact needs an attempt")
    expected = _attempt_documents(state, adapter_id)
    digest = _documents_digest(expected)
    folder = run_root(project) / stage / f"attempt-{attempt}"
    marker = folder / "status.json"
    if marker.exists():
        _verify_documents(folder, expected)
        return digest
    committed = {item["attempt"] for item in state["artifact_chain"]}
    require(attempt not in committed, "Run artifact was tampered")
    _verify_uncommitted_folder(folder, set(expected))
    for name in ("red.json", "green.json"):
        if name in expected:
            write_json(folder / name, expected[name], max_bytes=MAX_ARTIFACT_BYTES)
    write_json(marker, expected["status.json"], max_bytes=MAX_ARTIFACT_BYTES)
    _verify_documents(folder, expected)
    return digest


def verify_stage_attempt(project: Path, config: dict, state: dict,
                         fallback_adapter: str | None = None) -> str:
    """Verify all bound history, then recover only an unbound crash gap."""
    verify_artifact_history(project, config, state)
    attempt = state.get("attempts_started")
    require(type(attempt) is int and attempt > 0, "Attempt artifact needs an attempt")
    path = run_root(project) / state["stage"] / f"attempt-{attempt}" / "status.json"
    adapter = fallback_adapter or config["agent"]["default"]
    if path.exists():
        value = _read(path)
        _validate_status(value, state["stage"], attempt, config)
        adapter = value["adapter"]
    return record_stage_attempt(project, config, state, adapter)


def verify_artifact_history(project: Path, config: dict, state: dict) -> None:
    """Verify every committed attempt named by authoritative Stage state."""
    validate_artifact_chain(state)
    _verify_run_root(project, config)
    chain = {item["attempt"]: item["digest"] for item in state["artifact_chain"]}
    stage_root = run_root(project) / state["stage"]
    if not stage_root.exists():
        require(not chain, "Run artifact was tampered")
        return
    names = _directory_names(stage_root)
    committed_names = {f"attempt-{attempt}" for attempt in chain}
    allowed = set(committed_names)
    current = state["attempts_started"]
    if current > 0 and current not in chain:
        allowed.add(f"attempt-{current}")
    require(committed_names <= names <= allowed, "Run artifact was tampered")
    for attempt, digest in chain.items():
        folder = stage_root / f"attempt-{attempt}"
        require("status.json" in _directory_names(folder),
                "Run artifact was tampered")
        status = _read(folder / "status.json")
        _validate_status(status, state["stage"], attempt, config)
        expected_names = {"status.json"}
        if status["status"] == "STAGE_GREEN":
            expected_names.update({"red.json", "green.json"})
        documents = _read_documents(folder, expected_names)
        require(_documents_digest(documents) == digest,
                "Run artifact was tampered")


def record_final_run(project: Path, config: dict, coordinator: dict) -> str:
    """Create or verify final evidence and return its canonical digest."""
    require(coordinator.get("status") == "PROJECT_GREEN",
            "Final artifact requires authoritative project green state")
    _verify_run_root(project, config)
    completed = [stage["id"] for stage in stage_order(config)
                 if stage["id"] in coordinator["stages"]]
    evidence = safe_final_evidence(coordinator["final"], config, completed)
    digests = [coordinator["stages"][item]["handoff"]["result_digest"]
               for item in completed]
    expected = {"protocol": FINAL_PROTOCOL, "status": "PROJECT_GREEN",
                "completed_stages": completed, "stage_result_digests": digests,
                "judges": evidence}
    digest = _documents_digest({"evidence.json": expected})
    folder = run_root(project) / "final"
    path = folder / "evidence.json"
    bound = coordinator.get("final_artifact_digest")
    require(bound is None or bound == digest, "Run artifact was tampered")
    if path.exists():
        _verify_documents(folder, {"evidence.json": expected})
        return digest
    require(bound is None, "Run artifact was tampered")
    _verify_uncommitted_folder(folder, {"evidence.json"})
    write_json(path, expected, max_bytes=MAX_ARTIFACT_BYTES)
    _verify_documents(folder, {"evidence.json": expected})
    return digest


def attempt_metrics(project: Path, config: dict) -> list[dict]:
    """Return adapter-neutral attempt metrics in deterministic Stage order."""
    metrics = []
    maximum = config["budgets"]["attempts_per_stage"]
    for stage in stage_order(config):
        for attempt in range(1, maximum + 1):
            path = run_root(project) / stage["id"] / f"attempt-{attempt}" / "status.json"
            if path.exists():
                value = _read(path)
                _validate_status(value, stage["id"], attempt, config)
                metrics.append({name: value[name] for name in (
                    "stage", "attempt", "adapter", "status",
                    "finished_at_ms", "cumulative_duration_ms",
                )})
    return metrics


def _attempt_documents(state: dict, adapter_id: str) -> dict[str, dict]:
    failure = None
    if state.get("last_failure_fingerprint") is not None:
        failure = {"code": state.get("last_failure_code"),
                   "fingerprint": state["last_failure_fingerprint"]}
    status = {"protocol": ATTEMPT_PROTOCOL, "stage": state["stage"],
              "attempt": state["attempts_started"], "adapter": adapter_id,
              "status": state["status"], "finished_at_ms": state["last_wall_time_ms"],
              "cumulative_duration_ms": state["consumed_ms"], "failure": failure}
    documents = {"status.json": status}
    if state["status"] == "STAGE_GREEN":
        result = safe_result(state["result"], state["stage"])
        documents["red.json"] = {"protocol": ATTEMPT_PROTOCOL,
                                  "evidence": result["red"]}
        documents["green.json"] = {
            "protocol": ATTEMPT_PROTOCOL, "changes": result["changes"],
            "judges": [*result["l1"], *result["l2"]],
        }
    return documents


def _documents_digest(documents: dict[str, dict]) -> str:
    encoded = dumps_strict_json(documents, "Run artifact set", sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_documents(folder: Path, names: set[str]) -> dict[str, dict]:
    require(_directory_names(folder) == names, "Run artifact was tampered")
    return {name: _read(folder / name) for name in sorted(names)}


def _verify_documents(folder: Path, expected: dict[str, dict]) -> None:
    actual = _read_documents(folder, set(expected))
    require(actual == expected, "Run artifact was tampered")


def _verify_uncommitted_folder(folder: Path, expected: set[str]) -> None:
    if not folder.exists():
        return
    names = _directory_names(folder)
    require(names <= expected, "Run artifact was tampered")
    for name in names:
        _read(folder / name)


def _verify_run_root(project: Path, config: dict) -> None:
    root = run_root(project)
    if not root.exists():
        return
    allowed = {stage["id"] for stage in stage_order(config)} | {"final"}
    require(_directory_names(root) <= allowed, "Run artifact was tampered")


def _directory_names(folder: Path) -> set[str]:
    try:
        status = folder.lstat()
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        require(stat.S_ISDIR(status.st_mode) and not folder.is_symlink()
                and not getattr(status, "st_file_attributes", 0) & reparse,
                "Run artifact was tampered")
        return {item.name for item in folder.iterdir()}
    except StoryLoopError:
        raise
    except OSError as exc:
        raise StoryLoopError("Cannot inspect run artifacts") from exc


def _validate_status(value: dict, stage: str, attempt: int, config: dict) -> None:
    fields(value, {"protocol", "stage", "attempt", "adapter", "status",
                   "finished_at_ms", "cumulative_duration_ms", "failure"})
    require(value["protocol"] == ATTEMPT_PROTOCOL and value["stage"] == stage
            and value["attempt"] == attempt, "Invalid run artifact identity")
    require(value["adapter"] in config["agent"]["adapters"],
            "Invalid run artifact adapter")
    require(type(value["finished_at_ms"]) is int
            and type(value["cumulative_duration_ms"]) is int,
            "Invalid run artifact metrics")


def _read(path: Path) -> dict:
    try:
        status = path.lstat()
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        require(stat.S_ISREG(status.st_mode) and status.st_nlink == 1
                and not getattr(status, "st_file_attributes", 0) & reparse,
                "Run artifact must be a regular file")
        require(status.st_size <= MAX_ARTIFACT_BYTES, "Run artifact exceeds size limit")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            current = os.fstat(handle.fileno())
            require((status.st_dev, status.st_ino) == (current.st_dev, current.st_ino),
                    "Run artifact changed while opening")
            content = handle.read(MAX_ARTIFACT_BYTES + 1)
        require(len(content) <= MAX_ARTIFACT_BYTES, "Run artifact exceeds size limit")
        return loads_strict_json(content.decode("utf-8-sig"), "Run artifact",
                                 MAX_ARTIFACT_BYTES)
    except StoryLoopError:
        raise
    except (OSError, UnicodeError) as exc:
        raise StoryLoopError("Cannot read run artifact") from exc