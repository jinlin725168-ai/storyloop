"""Skill release gate (roadmap D2): a skill version counts as verified only with evaluation evidence.

Each packaged skill declares representative cases in evals/skills/<name>/cases.json. A results
file evals/skills/<name>/<version>.json records, for that exact content (sha256 over the skill's
files), each case's verdict, evidence and reviewer. A version is "verified" when every declared
case passed; "pending" when cases are declared but not all passed for the current content;
"undeclared" when the skill has no cases. Evidence for other content never counts.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / "evals" / "skills"
VERDICTS = {"passed", "failed"}


def skill_sha256(skill: dict) -> str:
    """Digest of a packaged skill's files (path and bytes), the content its evidence must match."""
    digest = hashlib.sha256()
    for relative in skill["files"]:
        # Line endings are normalized: a CRLF checkout (Windows CI) is the same content.
        content = (skill["root"] / relative).read_bytes().replace(b"\r\n", b"\n")
        digest.update(relative.encode("utf-8") + b"\0" + content + b"\0")
    return digest.hexdigest()


def cases(name: str) -> list[dict] | None:
    path = EVALS / name / "cases.json"
    return json.loads(path.read_text(encoding="utf-8"))["cases"] if path.is_file() else None


def results(name: str, version: str) -> dict | None:
    path = EVALS / name / f"{version}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def status(name: str, skill: dict) -> dict:
    """{state, version, passed, missing} for the skill's current version and content."""
    declared = cases(name)
    if declared is None:
        return {"state": "undeclared", "version": skill["version"], "passed": [], "missing": []}
    record = results(name, skill["version"]) or {}
    current = record.get("skill_sha256") == skill_sha256(skill)
    passed = sorted(item["case"] for item in record.get("results", [])
                    if current and item.get("verdict") == "passed")
    missing = sorted({case["key"] for case in declared} - set(passed))
    return {"state": "verified" if not missing else "pending", "version": skill["version"],
            "passed": passed, "missing": missing}
