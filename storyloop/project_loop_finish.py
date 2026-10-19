"""The idempotent completion stage: implementation commit, report, documents, merge, receipt.

Every step is persisted before the next one starts (VERIFIED, MERGED, RECEIPTING, FINALIZING,
PASSED), so a crash at any point resumes with `storyloop run` without calling an agent again or
committing twice.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path

from .answers import load_answers
from .baseline import _git, advance_repository_head
from .change_report import correction_view, generate_change_report
from .manual_acceptance import load_manual_acceptance, pending_manual_criteria
from .models import require
from .openspec_docs import capability_of, merge_into_main_spec, render_decisions, validate_with_cli
from .project_boundaries import audit_project_changes
from .project_git import commit_paths, current_branch, finish_feature
from .project_loop_gates import design_plan
from .project_loop_support import save_run, set_change_status
from .project_receipt import ensure_merge_receipt, verify_verified_artifacts
from .smoke_runner import implementation_digest
from .storage import load_json, write_json
from .telemetry import summarize


def complete_change(
    project: Path, change: dict, state: dict, boundary: dict,
    layers: dict, review: dict,
) -> dict:
    change_id = change["change_id"]
    subject = f"feat({change_id}): implement approved product change"
    # A completion that crashed after writing docs, specs, UI assets or reports left engine
    # outputs behind; discard them so this attempt regenerates everything from the same state.
    _discard_engine_outputs(project, change_id)
    final_boundary = _without_engine_outputs(audit_project_changes(project, change_id), change_id)
    boundary = _without_engine_outputs(boundary, change_id)
    digest = implementation_digest(project)
    manual = [record for record in load_manual_acceptance(project, change_id)
              if record.get("candidate_sha256") == digest]
    require(not pending_manual_criteria(change, manual, digest),
            "Manual acceptance is missing for the code being reported")
    already = _implementation_already_committed(project, subject, final_boundary)
    if already:
        implementation = {"commit": already}  # crash after the commit: adopt it, do not demand new changes
    else:
        require(final_boundary == boundary and not final_boundary["violations"],
                "Workspace changed after final boundary audit")
        changed_paths = [item["path"] for item in final_boundary["allowed"]]
        require(bool(changed_paths), "Passing implementation produced no auditable code changes")
        implementation = commit_paths(project, changed_paths, subject)
    telemetry = {**summarize(project, change_id), "attempts": state["attempt"],
                 "loop_ms": _loop_ms(state)}
    state["review"] = review
    save_run(project, state)
    report_path = generate_change_report(
        project, change_id, implementation["commit"], layers, review,
        manual_acceptance=manual, telemetry=telemetry,
        oracle=state.get("oracle"), skills_used=state.get("skills_used"),
        correction=correction_view(state, change),
    )
    state["manual_acceptance"] = manual
    state["telemetry"] = telemetry
    documents = _write_openspec_artifacts(project, change, state, manual)
    report_commit = commit_paths(
        project, list(dict.fromkeys([report_path.relative_to(project).as_posix(),
                                     report_path.with_suffix(".md").relative_to(project).as_posix(), *documents])),
        f"docs({change_id}): add acceptance evidence report",
    )
    state.update({"status": "VERIFIED", "implementation_commit": implementation["commit"],
                  "report_commit": report_commit["commit"], "report": str(report_path),
                  "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
                  "acceptance_evidence_sha256": layers["acceptance"]["evidence_sha256"], "review": review})
    save_run(project, state)
    return finish_verified(project, change, state)


def finish_verified(project: Path, change: dict, state: dict) -> dict:
    _verify_verified_state(project, state)
    merged = finish_feature(project, state["feature_branch"], state["source_branch"])
    state.update({"status": "MERGED", "merge_commit": merged["merge_commit"]})
    save_run(project, state)
    return finish_merged(project, change, state)


def finish_merged(project: Path, change: dict, state: dict) -> dict:
    require(current_branch(project) == state["source_branch"],
            "Merged run must finish on its source branch")
    require(_git(project, "rev-parse", "HEAD").strip() == state["merge_commit"],
            "Source branch changed after verified merge")
    state["status"] = "RECEIPTING"
    save_run(project, state)
    return finish_receipting(project, change, state)


def finish_receipting(project: Path, change: dict, state: dict) -> dict:
    integration = ensure_merge_receipt(project, state)
    advance_repository_head(project, integration)
    from .readiness_lineage import advance_readiness
    advance_readiness(project, {**state, "integration_commit": integration})
    state.update({"status": "FINALIZING", "integration_commit": integration,
                  "report_sha256": hashlib.sha256(Path(state["report"]).read_bytes()).hexdigest()})
    save_run(project, state)
    return finalize_change(project, change, state)


def finalize_change(project: Path, change: dict, state: dict) -> dict:
    require(current_branch(project) == state["source_branch"]
            and _git(project, "rev-parse", "HEAD").strip() == state["integration_commit"],
            "Integrated source branch changed before finalization")
    advance_repository_head(project, state["integration_commit"])
    if change["status"] != "PASSED":
        set_change_status(project, change, "PASSED")
    state["status"] = "PASSED"
    save_run(project, state)
    return passed_result(state)


def _engine_output_roots(change_id: str) -> tuple[str, ...]:
    return (f"openspec/changes/{change_id}/design.md", f"openspec/changes/{change_id}/decisions.md",
            "openspec/specs/", "acceptance/ui/", f"reports/storyloop/{change_id}/")


def _discard_engine_outputs(project: Path, change_id: str) -> None:
    """Restore tracked engine outputs and delete untracked ones (they are regenerated on completion)."""
    roots = _engine_output_roots(change_id)
    status = _git(project, "status", "--porcelain=v1", "--untracked-files=all", "-z")
    for entry in filter(None, status.split("\0")):
        code, path = entry[:2], entry[3:]
        engine_owned = any(path == root or path.startswith(root) for root in roots) or (
            path.startswith("openspec/changes/") and path.endswith("/traceability.json")
            and not path.startswith(f"openspec/changes/{change_id}/"))
        if not engine_owned:
            continue
        target = project / path
        if code.strip() == "??":
            target.unlink(missing_ok=True)
        else:
            _git(project, "checkout", "--", path)
    for root in ("acceptance/ui", f"reports/storyloop/{change_id}"):
        directory = project / root
        for empty in sorted((p for p in directory.rglob("*") if p.is_dir()), reverse=True) if directory.is_dir() else []:
            if not any(empty.iterdir()):
                empty.rmdir()


def _without_engine_outputs(boundary: dict, change_id: str) -> dict:
    roots = _engine_output_roots(change_id)
    keep = lambda item: not any(item["path"] == root or item["path"].startswith(root) for root in roots)  # noqa: E731
    allowed = [i for i in boundary["allowed"] if keep(i)]
    violations = [i for i in boundary["violations"] if keep(i)]
    status = ("CLEAN" if not allowed and not violations else "ALLOWED" if not violations
              else boundary["status"])
    return {**boundary, "allowed": allowed, "violations": violations, "status": status,
            "base_proposal": boundary.get("base_proposal") if violations else None}


def _implementation_already_committed(project: Path, subject: str, boundary: dict) -> str | None:
    """HEAD is the implementation commit of this change and nothing is left to commit."""
    if boundary["allowed"] or boundary["violations"]:
        return None
    if _git(project, "log", "-1", "--format=%s").strip() != subject:
        return None
    if _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip():
        return None
    return _git(project, "rev-parse", "HEAD").strip()


def _write_openspec_artifacts(project: Path, change: dict, state: dict, manual: list[dict]) -> list[str]:
    """design.md, decisions.md and the living spec merge; returns the paths to commit."""
    change_id = change["change_id"]
    base = project / "openspec" / "changes" / change_id
    written: list[str] = []
    plan = design_plan(state)
    parts = [plan["design"].rstrip("\n")] if plan else []
    if state.get("design"):
        parts.append(("## Implementation record\n\n" if plan else "") + state["design"].rstrip("\n"))
    if parts:
        (base / "design.md").write_text("\n\n".join(parts) + "\n", encoding="utf-8")
        written.append(f"openspec/changes/{change_id}/design.md")
    decisions = render_decisions(change, load_answers(project, change_id), manual, state.get("resets") or [],
                                 state.get("repairs") or [], overrules=state.get("overrules") or [])
    (base / "decisions.md").write_text(decisions, encoding="utf-8")
    written.append(f"openspec/changes/{change_id}/decisions.md")
    capability = capability_of(change["product"], change_id)
    delta_path = base / "specs" / capability / "spec.md"
    if delta_path.is_file():
        main_path = project / "openspec" / "specs" / capability / "spec.md"
        current = main_path.read_text(encoding="utf-8") if main_path.is_file() else None
        merged, replaced = merge_into_main_spec(current, delta_path.read_text(encoding="utf-8"),
                                                capability, change_id)
        main_path.parent.mkdir(parents=True, exist_ok=True)
        main_path.write_text(merged, encoding="utf-8")
        written.append(f"openspec/specs/{capability}/spec.md")
        state.setdefault("openspec", {})["spec"] = validate_with_cli(project, "spec", capability)
        state["superseded"] = replaced
        written.extend(_mark_superseded(project, replaced, change_id))
    return written


def _mark_superseded(project: Path, replaced: list[dict], change_id: str) -> list[str]:
    """Record in older changes which of their scenarios the new change replaced."""
    written: list[str] = []
    for item in replaced:
        path = project / "openspec" / "changes" / item["change_id"] / "traceability.json"
        if not path.is_file():
            continue
        trace = load_json(path)
        touched = False
        names = set(item.get("superseded") or [])
        for entry in trace.get("criteria", []):
            if (entry.get("story_id") == item["story_id"] and entry.get("criterion_id") in names
                    and entry.get("superseded_by") != change_id):
                entry["superseded_by"] = change_id
                touched = True
        relative = f"openspec/changes/{item['change_id']}/traceability.json"
        if touched:
            write_json(path, trace, max_bytes=1_000_000)
        if relative not in written and (touched or _git(project, "status", "--porcelain", "--", relative).strip()):
            written.append(relative)
    return written


def _loop_ms(state: dict) -> int:
    started = state.get("started_at")
    if not isinstance(started, str):
        return 0
    try:
        begun = datetime.fromisoformat(started)
    except ValueError:
        return 0
    return max(int((datetime.now(timezone.utc) - begun).total_seconds() * 1000), 0)


def passed_result(state: dict) -> dict:
    return {"status": "PASSED", "change_id": state["change_id"],
            "attempts": state["attempt"],
            "implementation_commit": state["implementation_commit"],
            "report_commit": state["report_commit"], "merge_commit": state["merge_commit"],
            "integration_commit": state["integration_commit"],
            "branch": state["source_branch"], "report": state["report"]}


def _verify_verified_state(project: Path, state: dict) -> None:
    require(not _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "Verified feature worktree must remain clean")
    require(current_branch(project) == state["feature_branch"],
            "Verified feature branch is not checked out")
    require(_git(project, "rev-parse", state["feature_branch"]).strip() == state["report_commit"],
            "Verified feature branch changed after reporting")
    require(_git(project, "rev-parse", state["source_branch"]).strip() == state["source_head"],
            "Source branch changed after the feature run started")
    verify_verified_artifacts(project, state)
