"""Prove a candidate criterion detects the approved old target Git blob."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

from .baseline import _git
from .models import require
from .project_profile import layer_commands
from .smoke_runner import run_commands_at_commit
from .system_smoke import validate_manifest


def _blob_oid(project: Path, commit: str, relative: str) -> str:
    row = _git(project, "ls-tree", commit, "--", relative).strip()
    require(row.startswith("100644 blob "), f"Criterion input is not a regular Git blob: {relative}")
    return row.split(" ", 3)[2].split("\t", 1)[0]


def _blob_bytes(project: Path, oid: str) -> bytes:
    result = subprocess.run(["git", "cat-file", "blob", oid], cwd=project,
                            capture_output=True, check=False)
    require(result.returncode == 0, "Approved target blob is unavailable")
    return result.stdout


def _command(project: Path, candidate: str, ref: dict, profile: dict) -> list[list[str]]:
    if ref["kind"] == "regression":
        commands = layer_commands(profile, ref["layer"])
        require(commands, "Approved regression check is not configured")
        return commands
    manifest_oid = _blob_oid(project, candidate, "acceptance/smoke/manifest.json")
    manifest = validate_manifest(json.loads(_blob_bytes(project, manifest_oid)))
    matches = [item["command"] for item in manifest["tests"] if item["id"] == ref["id"]]
    require(len(matches) == 1, "Approved system smoke check is missing from candidate")
    return matches


def prove_target(project: Path, change: dict, state: dict, profile: dict, timeout: int) -> dict:
    """Run the candidate's own check twice, injecting one immutable source blob."""
    proof = change["product"]["base"]["target_proof"]
    source, candidate = state["source_head"], state["candidate_commit"]
    old_oid = _blob_oid(project, source, proof["violation_path"])
    tree = _git(project, "rev-parse", f"{candidate}^{{tree}}").strip()
    criterion_oids = {path: _blob_oid(project, candidate, path)
                      for path in proof["criterion_paths"]}
    commands = _command(project, candidate, proof["check_ref"], profile)
    violation = run_commands_at_commit(project, candidate, commands, timeout,
                                       replacements={proof["violation_path"]:
                                                     _blob_bytes(project, old_oid)})
    require(violation["status"] == "FAILED"
            and all(row["status"] == "FAIL" for row in violation["results"]),
            "Target probe did not fail as an ordinary test on the old blob")
    normal = run_commands_at_commit(project, candidate, commands, timeout)
    require(normal["status"] == "PASSED", "Target criterion did not pass on candidate")
    return {"source_head": source, "source_blob_oid": old_oid,
            "candidate_commit": candidate, "candidate_tree_oid": tree,
            "criterion_blob_oids": criterion_oids, "check_ref": proof["check_ref"],
            "commands": commands, "violation": violation, "candidate": normal,
            "contract_digest": state["contract_digest"]}


def run_candidate_smoke(project: Path, candidate: str, timeout: int) -> dict:
    manifest_oid = _blob_oid(project, candidate, "acceptance/smoke/manifest.json")
    manifest = validate_manifest(json.loads(_blob_bytes(project, manifest_oid)))
    commands = [item["command"] for item in manifest["tests"]]
    return run_commands_at_commit(project, candidate, commands, timeout)


