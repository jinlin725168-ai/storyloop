"""Named independent human review for the E1 base implementation channel."""

from __future__ import annotations

from pathlib import Path
import hashlib

from .baseline import _git
from .base_criteria import criteria_review_summary
from .models import require, text
from .project_receipt import _dirty_paths
from .run_guards import base_candidate_authors, trusted_base_evidence
from .run_state import run_path, save_run
from .run_stops import pause
from .storage import load_json, write_json


def pause_base_review(project: Path, change: dict, state: dict) -> dict:
    return pause(project, state, "BASE_REVIEW_REQUIRED",
                 criteria_summary=criteria_review_summary(project, change, state),
                 next=f"storyloop base-review <project> --change {change['change_id']} "
                      "--verdict pass --note '<核对内容>', then storyloop run")


def review_base_candidate(project: Path, change: dict, state: dict, restart) -> dict:
    review = state.get("base_review")
    if review is None:
        return pause_base_review(project, change, state)
    if review.get("verdict") == "fail":
        return restart(project, change, state, "Human candidate needs a new attempt", rejected=True)
    require(review.get("verdict") == "pass", "Independent base review is invalid")
    require(review.get("reviewer") not in base_candidate_authors(project, state),
            "Base reviewer must differ from implementation author")
    return pause(project, state, "MANUAL_ACCEPTANCE_REQUIRED",
                 candidate_sha256=state["candidate_sha256"], pending=["BASE-ACCEPT"],
                 next=f"storyloop accept <project> --change {change['change_id']} "
                      "--criterion BASE-ACCEPT --note '<核对内容>', then storyloop run")


def record_human_review(project: Path, change_id: str, verdict: str, note: str) -> dict:
    state = load_json(run_path(project, change_id))
    require(state.get("status") == "BASE_REVIEW_REQUIRED",
            "Base review needs a verified green candidate")
    require(verdict in {"pass", "fail"}, "Base review verdict must be pass or fail")
    require(_git(project, "rev-parse", "HEAD").strip() == state.get("candidate_commit"),
            "Base candidate changed before review")
    require(not _dirty_paths(project), "Base review needs a clean candidate worktree")
    evidence = trusted_base_evidence(project, state)
    require(evidence is not None and evidence.get("status") == "PASSED"
            and evidence.get("candidate_commit") == state["candidate_commit"]
            and evidence.get("candidate_sha256") == state.get("candidate_sha256"),
            "Base review needs a verified green candidate")
    reviewer = text(_git(project, "config", "user.name").strip(), "reviewer")
    require(reviewer not in base_candidate_authors(project, state),
            "Base reviewer must differ from every implementation author")
    record = {"version": 1, "change_id": change_id, "verdict": verdict,
              "note": text(note, "review note"), "reviewer": reviewer,
              "candidate_commit": state["candidate_commit"],
              "candidate_sha256": state["candidate_sha256"], "mode": "human_e1"}
    path = project / ".storyloop/state/base-reviews" / f"{change_id}.json"
    write_json(path, record, max_bytes=1_000_000)
    state["base_review"] = record
    state["base_review_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    save_run(project, state)
    return {"status": "REVIEW_RECORDED", **record}
