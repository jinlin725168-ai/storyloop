"""Crash-safe verification and persistence of feature merge receipts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .baseline import _git
from .models import require
from .smoke import RESULT_EVIDENCE
from .project_git import commit_paths, current_branch
from .storage import write_bytes_atomic


def ensure_merge_receipt(project: Path, state: dict, receipt_bytes: tuple[bytes, bytes]) -> str:
    """Create or verify the unique report-only child of the verified merge."""
    require(current_branch(project) == state["source_branch"],
            "Merge receipt must finish on its source branch")
    head = _git(project, "rev-parse", "HEAD").strip()
    paths = _report_paths(project, state)
    authoritative, raw = _authoritative_report(project, state, paths[0])
    require(hashlib.sha256(raw).hexdigest() == state["report_sha256"],
            "Verified report commit does not match its protected digest")
    _require_receipt_bytes(authoritative, state["merge_commit"], receipt_bytes)
    verify_evidence_digest(project, state)
    _require_merge_tree(project, state)
    if head == state["merge_commit"]:
        require(_dirty_paths(project) <= set(paths),
                "Merge receipt recovery found unrelated workspace changes")
        report = Path(state["report"])
        write_bytes_atomic(report, receipt_bytes[0])
        write_bytes_atomic(report.with_suffix(".md"), receipt_bytes[1])
        return commit_paths(
            project, paths, f"docs({state['change_id']}): record verified merge"
        )["commit"]
    _verify_existing_receipt(project, state, head, paths, receipt_bytes)
    return head


def authoritative_report(project: Path, state: dict) -> dict:
    """Read the pre-merge report from its verified commit for upper-layer rendering."""
    report_path = _report_paths(project, state)[0]
    report, raw = _authoritative_report(project, state, report_path)
    require(hashlib.sha256(raw).hexdigest() == state["report_sha256"],
            "Verified report commit does not match its protected digest")
    return report


def _require_receipt_bytes(source: dict, merge_commit: str,
                           receipt_bytes: tuple[bytes, bytes]) -> None:
    require(isinstance(receipt_bytes, tuple) and len(receipt_bytes) == 2
            and all(isinstance(value, bytes) for value in receipt_bytes),
            "Invalid merge receipt bytes")
    require(len(receipt_bytes[0]) <= 8_000_000, "Acceptance report exceeds size limit")
    try:
        report = json.loads(receipt_bytes[0].decode("utf-8"))
    except (UnicodeError, ValueError):
        require(False, "Invalid merge receipt bytes")
    require(report == {**source, "merge_commit": merge_commit},
            "Merge receipt does not match the verified report")


def verify_verified_artifacts(project: Path, state: dict) -> None:
    report = Path(state["report"])
    require(report.is_file()
            and hashlib.sha256(report.read_bytes()).hexdigest() == state["report_sha256"],
            "Verified acceptance report changed before merge")
    verify_evidence_digest(project, state)


def recover_completed_merge(project: Path, state: dict) -> str:
    head = _git(project, "rev-parse", "HEAD").strip()
    parents = _git(project, "rev-list", "--parents", "-n", "1", head).split()
    require(len(parents) == 3 and state["source_head"] in parents[1:]
            and state["report_commit"] in parents[1:],
            "Source branch does not contain the expected verified merge")
    require(not _dirty_paths(project), "Recovered merge worktree must remain clean")
    _require_merge_tree(project, {**state, "merge_commit": head})
    verify_verified_artifacts(project, state)
    return head


def verify_evidence_digest(project: Path, state: dict) -> None:
    evidence_name = ("base-candidate-green.json" if state["change_id"].startswith("BASE-")
                     else RESULT_EVIDENCE)
    evidence = project / ".storyloop/evidence" / state["change_id"] / evidence_name
    require(evidence.is_file()
            and hashlib.sha256(evidence.read_bytes()).hexdigest()
            == state["acceptance_evidence_sha256"],
            "Verified smoke evidence changed before merge")


def _verify_existing_receipt(
    project: Path, state: dict, head: str, paths: list[str], receipt_bytes: tuple[bytes, bytes],
) -> None:
    parents = _git(project, "rev-list", "--parents", "-n", "1", head).split()
    changed = set(filter(None, _git(
        project, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", head
    ).split("\0")))
    actual = (Path(state["report"]).read_bytes(),
              Path(state["report"]).with_suffix(".md").read_bytes())
    require(len(parents) == 2 and parents[1] == state["merge_commit"]
            and changed == set(paths) and not _dirty_paths(project)
            and actual == receipt_bytes,
            "Cannot recover the merge receipt safely")


def _authoritative_report(
    project: Path, state: dict, report_path: str,
) -> tuple[dict, bytes]:
    raw = _git(project, "show", f"{state['report_commit']}:{report_path}").encode("utf-8")
    try:
        report = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Verified report commit contains invalid JSON") from exc
    require(isinstance(report, dict), "Verified report commit contains invalid JSON")
    return report, raw


def _require_merge_tree(project: Path, state: dict) -> None:
    require(_git(project, "rev-parse", f"{state['merge_commit']}^{{tree}}").strip()
            == _git(project, "rev-parse", f"{state['report_commit']}^{{tree}}").strip(),
            "Recovered merge tree differs from the verified report commit")


def _report_paths(project: Path, state: dict) -> list[str]:
    report = Path(state["report"])
    return [report.relative_to(project).as_posix(),
            report.with_suffix(".md").relative_to(project).as_posix()]


def _dirty_paths(project: Path) -> set[str]:
    outputs = [_git(project, "diff", "--no-renames", "--name-only", "-z", "HEAD"),
               _git(project, "ls-files", "--others", "--exclude-standard", "-z")]
    return {item.replace("\\", "/") for output in outputs
            for item in output.split("\0") if item}
