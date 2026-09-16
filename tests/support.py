"""Shared fixtures for the external contract tests."""
import json
import sys
from pathlib import Path


def product(manual=False):
    return {"version": 1, "goal": "Reserve a meeting room without overlap", "non_goals": [],
            "questions": [], "stories": [{"id": "ST-1", "title": "Reserve a room", "as_a": "employee",
            "want": "reserve a free room", "so_that": "meetings do not collide", "rules": ["No overlap"],
            "out_of_scope": [], "depends_on": [], "acceptance": [{"id": "AC-1", "given": "a reservation exists",
            "when": "another overlaps", "then": "reject it", "kind": "manual" if manual else "automated"}]}]}


def check(kind="acceptance", status="pass"):
    body = {"protocol": "storyloop.check.v1", "results": [{"id": "overlap", "status": status, "message": "observed"}]}
    code = "import json,sys; print(" + repr(json.dumps(body)) + "); sys.exit(" + ("0" if status == "pass" else "1") + ")"
    return {"id": "CHECK-1", "story_id": "ST-1", "kind": kind, "criteria": ["AC-1"] if kind == "acceptance" else [],
            "command": [sys.executable, "-c", code], "expected_tests": ["overlap"], "timeout_seconds": 3}


def config():
    return {"version": 1, "adapter": "manual", "agent_command": [], "max_attempts": 3,
            "max_seconds": 60, "agent_timeout_seconds": 10, "check_timeout_seconds": 10,
            "repeated_failure_limit": 2}


def write(path, value):
    Path(path).write_text(json.dumps(value), encoding="utf-8")
