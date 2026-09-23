"""Crash-safe verification and persistence of feature merge receipts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .baseline import _git
from .change_report import merge_receipt_bytes, record_merge_commit
from .models import require
from .smoke import RESULT_EVIDENCE
from .project_git import commit_paths, current_branch


def ensure_merge_receipt(project: Path, state: dict) -> str:
    """Create or verify the unique report-only child of the verified merge."""
    require(current_branch(project) == state["source_branch"],
            "Merge receipt must finish on its source branch")
    head = _git(project, "rev-parse", "HEAD").strip()
    paths = _report_paths(project, state)
    authoritative, raw = _authoritative_report(project, state, paths[0])
    require(hashlib.sha256(raw).hexdigest() == state["report_sha256"],
            "Verified report commit does not match its protected digest")
    verify_evidence_digest(project, state)
    _require_merge_tree(project, state)
    if head == state["merge_commit"]:
        require(_dirty_paths(project) <= set(paths),
                "Merge receipt recovery found unrelated workspace changes")
        record_merge_commit(Path(state["report"]), state["merge_commit"], authoritative)
        return commit_paths(
            project, paths, f"docs({state['change_id']}): record verified merge"
        )["commit"]
    _verify_existing_receipt(project, state, head, paths, authoritative)
    return head


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
    evidence = project / ".storyloop/evidence" / state["change_id"] / RESULT_EVIDENCE
    require(evidence.is_file()
            and hashlib.sha256(evidence.read_bytes()).hexdigest()
            == state["acceptance_evidence_sha256"],
            "Verified smoke evidence changed before merge")


def _verify_existing_receipt(
    project: Path, state: dict, head: str, paths: list[str], authoritative: dict,
) -> None:
    parents = _git(project, "rev-list", "--parents", "-n", "1", head).split()
    changed = set(filter(None, _git(
        project, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", head
    ).split("\0")))
    expected_json, expected_markdown = merge_receipt_bytes(
        authoritative, state["merge_commit"]
    )
    actual = (Path(state["report"]).read_bytes(),
              Path(state["report"]).with_suffix(".md").read_bytes())
    require(len(parents) == 2 and parents[1] == state["merge_commit"]
            and changed == set(paths) and not _dirty_paths(project)
            and actual == (expected_json, expected_markdown),
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
    outputs = [_git(project, "diff", "--name-only", "-z", "HEAD"),
               _git(project, "ls-files", "--others", "--exclude-standard", "-z")]
    return {item.replace("\\", "/") for output in outputs
            for item in output.split("\0") if item}
