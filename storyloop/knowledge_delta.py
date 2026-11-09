"""Human-authorized, exact-path foundation updates after a verified PASSED change."""

from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path

from .baseline import BASELINE_PATH, _git, load_baseline, verify_baseline
from .base_upgrade import _next_version
from .changes import load_change
from .drafts import load_draft
from .facts import valid_location
from .intervention_events import append_event
from .models import identifier, require, text
from .path_security import repository_relative, safe_target
from .project_git import commit_paths, exact_child_commit
from .project_loop_support import save_run
from .project_receipt import verify_evidence_digest
from .run_state import run_path
from .storage import load_json, locked, write_bytes_atomic, write_json
from .project_agents import invoke_project_agent

ROOT = ".storyloop/state/knowledge/proposals"
MAX_UPDATES = 5
MAX_CONTENT = 512_000


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _head(project: Path) -> str:
    return _git(project, "rev-parse", "HEAD").strip()


def _run_state(project: Path, change_id: str) -> dict:
    return load_json(run_path(project, change_id))


def _proposal_path(project: Path, proposal_id: str) -> Path:
    identifier(proposal_id, "proposal id")
    require(proposal_id.startswith("K-"), "Invalid knowledge proposal id")
    return safe_target(project, f"{ROOT}/{proposal_id}.json", "knowledge proposal")


def _identity(change_id: str, head: str, updates: list[dict]) -> str:
    bound = json.dumps({"change_id": change_id, "head": head, "updates": updates},
                       ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"K-{change_id}-{_sha(bound.encode('utf-8'))[:32]}"


def _passed_context(project: Path, change_id: str) -> tuple[dict, dict]:
    state = _run_state(project, change_id)
    change = load_change(project, change_id)
    require(state.get("status") == change.get("status") == "PASSED" and change.get("approval"),
            "Knowledge proposals require a PASSED approved change")
    report = Path(state["report"])
    require(report.is_absolute() and report.is_relative_to(project), "Unsafe knowledge report path")
    report = safe_target(project, report.relative_to(project).as_posix(), "knowledge report")
    require(report.is_file() and _sha(report.read_bytes()) == state.get("report_sha256"),
            "PASSED report digest changed")
    body = load_json(report)
    require(body.get("status") == "PASSED" and body.get("change_id") == change_id
            and body.get("implementation_commit") == state.get("implementation_commit"),
            "PASSED report does not match the implementation")
    _git(project, "merge-base", "--is-ancestor", state["implementation_commit"], "HEAD")
    _git(project, "merge-base", "--is-ancestor", state["report_commit"], "HEAD")
    committed = json.loads(_git(project, "show", f"{state['report_commit']}:"
                           f"{report.relative_to(project).as_posix()}"))
    require(body == {**committed, "merge_commit": state["merge_commit"]},
            "PASSED report no longer matches its verified commit")
    verify_evidence_digest(project, state)
    return state, change


def _checked_target(project: Path, baseline: dict, relative: object) -> tuple[str, Path]:
    path = repository_relative(relative, "knowledge path")
    require(path.startswith("openspec/foundation/") and path in baseline["foundation_paths"]
            and path in baseline["files"], "Knowledge path must be an existing protected foundation file")
    target = safe_target(project, path, "knowledge path")
    require(target.is_file(), "Knowledge path must be a regular non-symlink file")
    return path, target


def _existing_errata(project: Path, source: str, claim: str, correction: str) -> str | None:
    root = safe_target(project, ".storyloop/drafts/errata", "errata drafts", target_directory=True)
    for directory in root.iterdir() if root.is_dir() else []:
        if not directory.is_dir():
            continue
        draft = load_draft(project, f"errata/{directory.name}")
        payload = draft.get("payload") or {}
        if payload.get("claim") == claim and payload.get("correction") == correction \
                and source in (payload.get("evidence") or []):
            return f"errata/{draft['id']}"
    return None


def _candidate(project: Path, baseline: dict, item: dict) -> dict:
    require(isinstance(item, dict), "Knowledge update must be an object")
    path, target = _checked_target(project, baseline, item.get("path"))
    source = text(item.get("source"), "knowledge source")
    require(valid_location(project, source), "Knowledge source must name a current code line")
    source_path = safe_target(project, source.rsplit(":", 1)[0], "knowledge source")
    claim = item.get("claim") or ""
    require(isinstance(claim, str), "Knowledge claim must be text")
    correction = text(item.get("correction"), "knowledge correction")
    content = item.get("content")
    require(isinstance(content, str) and bool(content.strip())
            and len(content.encode("utf-8")) <= MAX_CONTENT, "Invalid knowledge content")
    old = target.read_bytes()
    require(not claim or claim in old.decode("utf-8"), "Knowledge claim is not in the target")
    require(correction in content and content.encode("utf-8") != old,
            "Knowledge update must change the target with its stated correction")
    diff = "".join(difflib.unified_diff(old.decode("utf-8").splitlines(keepends=True),
                                        content.splitlines(keepends=True),
                                        fromfile=f"a/{path}", tofile=f"b/{path}"))
    return {"path": path, "source": source, "source_commit": _head(project),
            "source_sha256": _sha(source_path.read_bytes()),
            "claim": claim, "correction": correction, "original_sha256": _sha(old),
            "content_sha256": _sha(content.encode("utf-8")), "content": content, "diff": diff}


def propose_knowledge(project: Path, change_id: str) -> dict:
    """Ask a read-only review role for stable facts, then save exact candidate bytes."""
    from .guarded_calls import read_only_call
    project = Path(project).resolve()
    change_id = identifier(change_id, "change id")
    state, change = _passed_context(project, change_id)
    verify_baseline(project)
    plan = project / f"openspec/changes/{change_id}/routing-plan.json"
    evidence = project / ".storyloop/evidence" / change_id / "acceptance-result.json"
    payload = {"mode": "knowledge-propose", "change": {"change_id": change_id},
               "product": change["product"], "approval": change["approval"],
               "plan": load_json(plan) if plan.is_file() else None,
               "acceptance_evidence": load_json(evidence),
               "implementation_commit": state["implementation_commit"],
               "head": _head(project)}
    response = read_only_call(project, change_id,
                              lambda: invoke_project_agent(project, "review", payload))
    require(isinstance(response, dict) and isinstance(response.get("updates"), list)
            and len(response["updates"]) <= MAX_UPDATES, "Invalid knowledge proposal response")
    baseline = load_baseline(project)
    updates, links = [], []
    for item in response["updates"]:
        candidate = _candidate(project, baseline, item)
        duplicate = _existing_errata(project, candidate["source"], candidate["claim"],
                                     candidate["correction"]) if candidate["claim"] else None
        if duplicate:
            links.append(duplicate)
        else:
            updates.append(candidate)
    require(len({item["path"] for item in updates}) == len(updates), "Duplicate knowledge path")
    if not updates:
        return {"status": "NO_KNOWLEDGE_DELTA", "change_id": change_id, "errata_links": links}
    proposal_id = _identity(change_id, _head(project), updates)
    path = _proposal_path(project, proposal_id)
    require(not path.exists(), "Knowledge proposal already exists")
    write_json(path, {"version": 1, "status": "PROPOSED", "proposal_id": proposal_id,
                      "change_id": change_id, "head": _head(project),
                      "baseline_version": baseline["baseline_version"],
                      "updates": updates, "errata_links": links}, max_bytes=MAX_UPDATES * MAX_CONTENT + 100_000)
    return {"status": "KNOWLEDGE_PROPOSED", "proposal_id": proposal_id, "change_id": change_id,
            "updates": [{key: item[key] for key in ("path", "source", "source_commit",
                                                   "original_sha256", "diff")}
                        for item in updates], "errata_links": links}


def _verify_update(project: Path, baseline: dict, item: dict) -> None:
    path, target = _checked_target(project, baseline, item.get("path"))
    require(_sha(target.read_bytes()) == item.get("original_sha256")
            and baseline["files"][path] == item["original_sha256"],
            "Knowledge target changed since proposal")
    source = item.get("source")
    require(isinstance(source, str) and valid_location(project, source),
            "Knowledge source location changed")
    source_path = safe_target(project, source.rsplit(":", 1)[0], "knowledge source")
    require(_sha(source_path.read_bytes()) == item.get("source_sha256"),
            "Knowledge source changed since proposal")
    require(_candidate(project, baseline, item) == item, "Knowledge diff differs from proposal bytes")


def _finalize(project: Path, proposal: dict, paths: list[str], commit: str) -> dict:
    baseline = load_baseline(project)
    if baseline["source_head"] == proposal["head"]:
        baseline["source_head"] = commit
        baseline["baseline_version"] = _next_version(baseline["baseline_version"])
        for item in proposal["updates"]:
            if item["path"] in paths:
                baseline["files"][item["path"]] = item["content_sha256"]
        write_json(project / BASELINE_PATH, baseline, max_bytes=8_000_000)
    else:
        require(baseline["source_head"] == commit, "BASE changed during knowledge approval")
    state = _run_state(project, proposal["change_id"])
    if not any(item.get("proposal_id") == proposal["proposal_id"] for item in
               state.get("intervention_events", []) if isinstance(item, dict)):
        append_event(state, "knowledge_approval", source="authorize-knowledge")
        state["intervention_events"][-1]["proposal_id"] = proposal["proposal_id"]
        save_run(project, state)
    result = {"status": "KNOWLEDGE_APPLIED", "proposal_id": proposal["proposal_id"],
              "change_id": proposal["change_id"], "paths": paths, "commit": commit,
              "baseline_version": baseline["baseline_version"]}
    proposal["status"], proposal["result"] = "APPLIED", result
    write_json(_proposal_path(project, proposal["proposal_id"]), proposal)
    return result


def _resume_authorized(project: Path, proposal: dict, paths: list[str]) -> dict:
    parent, head = proposal["head"], _head(project)
    selected = [item for item in proposal["updates"] if item["path"] in paths]
    if head == parent:
        dirty = [part[3:] for part in _git(project, "status", "--porcelain=v1", "-z").split("\0") if part]
        require(set(dirty) <= set(paths) and not _git(project, "diff", "--cached", "--no-renames",
                                                     "--name-only").strip(),
                "Workspace drift after knowledge authorization")
        for item in selected:
            path = safe_target(project, item["path"], "knowledge target")
            require(_sha(path.read_bytes()) in {item["original_sha256"], item["content_sha256"]},
                    "Knowledge target drift after authorization")
            if _sha(path.read_bytes()) == item["original_sha256"]:
                write_bytes_atomic(path, item["content"].encode("utf-8"))
        commit = commit_paths(project, paths, f"docs(foundation): 更新 {proposal['change_id']} 的知识")["commit"]
    else:
        require(exact_child_commit(project, parent, set(paths), head),
                "Knowledge commit differs from authorized exact paths")
        require(not _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
                "Workspace drift after knowledge commit")
        commit = head
    require(all(_sha(safe_target(project, item["path"], "knowledge target").read_bytes())
                == item["content_sha256"] for item in selected),
            "Knowledge bytes differ from authorized proposal")
    return _finalize(project, proposal, paths, commit)


def authorize_knowledge(project: Path, proposal_id: str, paths: list[str]) -> dict:
    """Bind exact paths before writing; replay the same proposal without a second commit/event."""
    project = Path(project).resolve()
    require(isinstance(paths, list) and all(isinstance(path, str) for path in paths),
            "Knowledge paths must be a list")
    normalized = [repository_relative(path, "knowledge path") for path in paths]
    require(len(set(normalized)) == len(normalized), "Duplicate authorized knowledge path")
    with locked(project):
        proposal = load_json(_proposal_path(project, proposal_id))
        require(proposal.get("proposal_id") == proposal_id, "Knowledge proposal identity changed")
        require(_identity(proposal.get("change_id"), proposal.get("head"), proposal.get("updates"))
                == proposal_id, "Knowledge proposal bytes changed after review")
        if proposal.get("status") == "APPLIED":
            require(normalized == proposal["result"]["paths"], "Authorization replay paths differ")
            return proposal["result"]
        if proposal.get("status") == "REJECTED":
            require(not normalized, "Rejected knowledge proposal cannot be approved")
            return proposal["result"]
        if not normalized:
            require(proposal.get("status") == "PROPOSED", "Cannot reject an active authorization")
            result = {"status": "KNOWLEDGE_REJECTED", "proposal_id": proposal_id,
                      "change_id": proposal["change_id"], "paths": []}
            proposal["status"], proposal["result"] = "REJECTED", result
            write_json(_proposal_path(project, proposal_id), proposal)
            return result
        offered = {item["path"] for item in proposal["updates"]}
        require(set(normalized) <= offered, "Authorization must name offered exact paths")
        baseline = load_baseline(project)
        for path in normalized:
            _checked_target(project, baseline, path)
        if proposal.get("status") == "PROPOSED":
            _passed_context(project, proposal["change_id"])
            verify_baseline(project)
            require(_head(project) == proposal["head"]
                    and baseline["baseline_version"] == proposal["baseline_version"],
                    "Knowledge proposal HEAD or BASE changed")
            for item in proposal["updates"]:
                _verify_update(project, baseline, item)
            proposal["status"], proposal["paths"] = "AUTHORIZED", normalized
            write_json(_proposal_path(project, proposal_id), proposal)
        else:
            require(proposal.get("status") == "AUTHORIZED" and normalized == proposal.get("paths"),
                    "Authorization replay paths differ")
        return _resume_authorized(project, proposal, normalized)
