"""External verdicts: missing, skipped and malformed evidence always block."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from .processutil import run_process, redact


def _classify(process: dict, expected: list[str]) -> tuple[str, str, list]:
    for key in ("error", "timed_out", "truncated"):
        if process[key]:
            return "BLOCKED", f"Check execution {key}", []
    try:
        value = json.loads(process["stdout"])
    except (ValueError, TypeError):
        return "BLOCKED", "Check did not emit exactly one protocol JSON object", []
    if not isinstance(value, dict) or set(value) != {"protocol", "results"}:
        return "BLOCKED", "Invalid check protocol fields", []
    if value["protocol"] != "storyloop.check.v1" or not isinstance(value["results"], list):
        return "BLOCKED", "Invalid check protocol", []
    rows = value["results"]
    if not _valid_rows(rows, expected):
        return "BLOCKED", "Missing, duplicate, unexpected or invalid test results", []
    rows = [{**row, "message": redact(row["message"])[:4000]} for row in rows]
    statuses = {row["status"] for row in rows}
    if statuses <= {"pass"} and process["returncode"] == 0:
        return "GREEN", "All named tests executed and passed", rows
    if statuses <= {"pass", "fail"} and "fail" in statuses and process["returncode"] == 1:
        return "RED", "Named behavioral assertions failed", rows
    return "BLOCKED", "Error, skipped test or inconsistent exit code", rows


def _valid_rows(rows: list, expected: list[str]) -> bool:
    if not rows or len(rows) != len(expected):
        return False
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "status", "message"}:
            return False
        if not isinstance(row["id"], str) or not isinstance(row["message"], str):
            return False
        if not isinstance(row["status"], str) or row["status"] not in {"pass", "fail", "error", "skip"}:
            return False
    return len({r["id"] for r in rows}) == len(rows) and {r["id"] for r in rows} == set(expected)


def run_check(project: Path, check: dict, timeout: float | None = None) -> dict:
    """Run a trusted approved check; protocol validity cannot prove test semantics."""
    replacements = {"{python}": sys.executable, "{workspace}": str(project / "workspace"), "{qa}": str(project / "qa")}
    command = []
    for argument in check["command"]:
        for token, replacement in replacements.items():
            argument = argument.replace(token, replacement)
        command.append(argument)
    start = time.monotonic()
    process = run_process(command, project / "workspace", timeout or check["timeout_seconds"])
    outcome, reason, rows = _classify(process, check["expected_tests"])
    return {"check_id": check["id"], "story_id": check["story_id"], "criteria": check["criteria"],
            "kind": check["kind"], "outcome": outcome, "reason": reason, "results": rows,
            "exit_code": process["returncode"], "seconds": round(time.monotonic() - start, 3)}
