"""Independent vendor review or a named single-vendor human substitute."""

from __future__ import annotations

from pathlib import Path
import hashlib
import re

from .baseline import _git, load_project_config
from .capability_protocol import normalize_vendor, observed_vendors
from .engine_source_safety import engine_warning
from .finding_disposition import dispose
from .guarded_calls import read_only_call
from .models import StoryLoopError, require, text
from .project_agents import invoke_project_agent
from .project_receipt import _dirty_paths
from .role_profiles import enabled, resolve_adapters, role_settings
from .run_guards import base_candidate_authors, trusted_base_evidence
from .run_state import run_path, save_run
from .run_stops import pause
from .storage import load_json, write_json


def require_distinct_vendors(implement: str | None, review: str | None) -> tuple[str, str]:
    """A missing or equal handshake vendor cannot establish independence."""
    require(isinstance(implement, str) and bool(implement.strip())
            and isinstance(review, str) and bool(review.strip()),
            "Base review vendor handshake is missing")
    pair = implement.strip().casefold(), review.strip().casefold()
    require(pair[0] != pair[1], "Base review vendor must differ from implement vendor")
    return pair


def vendor_context(project: Path, state: dict) -> dict:
    """Read the engine-observed handshake for the selected role adapters."""
    config = load_project_config(project)
    if not enabled(config):
        raise StoryLoopError("Agent base review needs configured adapters",
                             code="BASE_REVIEW_UNCONFIGURED")
    adapters = resolve_adapters(config)
    implement_adapter = state.get("agent_adapter")
    review_adapter = role_settings(config, "review", "heavy", base=True)["adapter"]
    try:
        capabilities = load_json(project / ".storyloop/state/adapters.json").get("adapters", {})
    except StoryLoopError:
        capabilities = {}
    vendors, known = observed_vendors(adapters, capabilities)
    implement_vendor = vendors.get(implement_adapter)
    require(implement_vendor == normalize_vendor(state.get("agent_vendor")) and implement_vendor,
            "Base implement vendor handshake changed after candidate creation")
    return {"implement_vendor": implement_vendor, "review_vendor": vendors.get(review_adapter),
            "review_adapter": review_adapter, "available_vendors": known}


def agent_review_context(project: Path, state: dict) -> tuple[str, dict | None]:
    """Classify the selected reviewer without hiding implementer binding errors."""
    try:
        context = vendor_context(project, state)
    except StoryLoopError as exc:
        if exc.code == "BASE_REVIEW_UNCONFIGURED" and not state.get("agent_adapter"):
            return "unknown", None
        raise
    if not context["review_vendor"]:
        return "unknown", context
    if context["implement_vendor"] != context["review_vendor"]:
        return "distinct", context
    if context["available_vendors"] is None:
        return "unknown", context
    return ("single" if single_vendor_only(project, state, context) else "reconfigure"), context


def agent_review_next(change_id: str, mode: str) -> str:
    if mode == "unknown":
        return "storyloop adapters check <project>"
    if mode == "reconfigure":
        return ("Set roles.review.adapter to a proven adapter from another vendor, then "
                f"storyloop run <project> --change {change_id}")
    if mode == "single":
        return (f"storyloop base-review <project> --change {change_id} --verdict pass "
                "--note '<核对内容>' --human-review '<至少 12 字理由>'")
    return f"storyloop run <project> --change {change_id}"


def pause_base_review(project: Path, change: dict, state: dict, restart) -> dict:
    if state.get("implementer", "human") == "agent":
        try:
            mode, _ = agent_review_context(project, state)
        except StoryLoopError as exc:
            return restart(project, change, state, str(exc), rejected=True)
        return pause(project, state, "BASE_REVIEW_REQUIRED",
                     next=agent_review_next(change["change_id"], mode), base_review_mode=mode)
    return pause(project, state, "BASE_REVIEW_REQUIRED",
                 next=f"storyloop base-review <project> --change {change['change_id']} "
                      "--verdict pass --note '<核对内容>', then storyloop run")


def review_base_candidate(project: Path, change: dict, state: dict, services, restart) -> dict:
    """Resume a saved review stop using one structured vendor decision."""
    review = state.get("base_review")
    if review is None:
        if state.get("implementer", "human") == "agent":
            try:
                mode, context = agent_review_context(project, state)
                if mode != "distinct":
                    return pause(project, state, "BASE_REVIEW_REQUIRED",
                                 next=agent_review_next(change["change_id"], mode),
                                 base_review_mode=mode)
                review = review_agent_candidate(project, change, state, services, context)
            except StoryLoopError as exc:
                return restart(project, change, state, str(exc), rejected=True)
        else:
            return pause_base_review(project, change, state, restart)
    if review.get("verdict") == "fail":
        if state.get("implementer", "human") == "agent":
            return pause(project, state, "BASE_REVIEW_REQUIRED",
                         next=f"storyloop reset <project> --change {change['change_id']} "
                              "--reason '<审查发现与重来原因>'")
        return restart(project, change, state, "Human candidate needs a new attempt", rejected=True)
    require(review.get("verdict") == "pass", "Independent base review is invalid")
    require(review.get("reviewer") not in base_candidate_authors(project, state),
            "Base reviewer must differ from implementation author")
    return pause(project, state, "MANUAL_ACCEPTANCE_REQUIRED",
                 candidate_sha256=state["candidate_sha256"], pending=["BASE-ACCEPT"],
                 next=f"storyloop accept <project> --change {change['change_id']} "
                      "--criterion BASE-ACCEPT --note '<核对内容>', then storyloop run")


def human_substitute_fields(project: Path, state: dict, reason: str) -> dict:
    """Permit named human review only if one known vendor can actually be configured."""
    from .base_agent_execution import agent_binding_changes
    require(not agent_binding_changes(project, state["change_id"],
                                      state.get("agent_control_binding")),
            "Base review handshake configuration changed after the agent call")
    context = vendor_context(project, state)
    require(context["available_vendors"] is not None
            and len(context["available_vendors"]) == 1,
            "Human review substitute requires exactly one proven adapter vendor")
    cleaned = " ".join(reason.split()) if isinstance(reason, str) else ""
    require(len(cleaned) >= 12, "Human review substitute reason needs at least 12 characters")
    verdict_only = re.sub(r"[\W_]+", "", cleaned.casefold(), flags=re.UNICODE)
    for token in ("pass", "passed", "fail", "failed", "approve", "approved", "审查通过",
                  "审核通过", "通过", "不通过", "同意", "已审查"):
        verdict_only = verdict_only.replace(token, "")
    require(bool(verdict_only), "Human review substitute reason must explain the review, "
            "not merely repeat its verdict")
    return {"mode": "human_substitute", "human_review_reason": cleaned,
            "implement_vendor": context["implement_vendor"],
            "plan_id": state["base_design"]["plan_id"],
            "design_sha256": state["base_design_sha256"],
            "contract_digest": state["contract_digest"]}


def review_agent_candidate(project: Path, change: dict, state: dict, services=None,
                           context: dict | None = None) -> dict:
    """Call the distinct reviewer read-only and bind its verdict to this attempt."""
    context = context if context is not None else vendor_context(project, state)
    implement_vendor, review_vendor = require_distinct_vendors(
        context["implement_vendor"], context["review_vendor"])
    payload = {"mode": "base-review", "change": change,
               "candidate_commit": state["candidate_commit"],
               "candidate_sha256": state["candidate_sha256"],
               "design_plan": state["base_design"]}
    operation = (lambda: services.review(project, payload)) if services else (
        lambda: invoke_project_agent(project, "review", payload))
    def source_guard() -> None:
        warning = engine_warning(state)
        if warning:
            state["engine_warning"] = warning
    answer = read_only_call(project, change["change_id"], operation,
                            post_call_guard=source_guard)
    require(isinstance(answer, dict) and isinstance(answer.get("findings"), list),
            "Independent base agent review is invalid")
    disposition = dispose(answer["findings"],
                          load_project_config(project).get("review_thresholds", {}), [])
    blocking = disposition["fix"] + disposition["stop"] + disposition["human"]
    verdict = "fail" if blocking or answer.get("questions") else "pass"
    record = {"version": 1, "change_id": change["change_id"], "verdict": verdict,
              "note": answer.get("summary", "independent agent review"),
              "finding_disposition": disposition,
              "findings": blocking, "questions": answer.get("questions") or [],
              "reviewer": f"agent:{context['review_adapter']}", "mode": "agent",
              "review_adapter": context["review_adapter"],
              "review_vendor": review_vendor, "implement_vendor": implement_vendor,
              "candidate_commit": state["candidate_commit"],
              "candidate_sha256": state["candidate_sha256"],
              "candidate_tree_oid": _git(project, "rev-parse",
                                         f"{state['candidate_commit']}^{{tree}}").strip(),
              "contract_digest": state["contract_digest"],
              "plan_id": state["base_design"]["plan_id"],
              "design_sha256": state["base_design_sha256"]}
    path = project / ".storyloop/state/base-reviews" / f"{change['change_id']}.json"
    write_json(path, record, max_bytes=1_000_000)
    state["base_review"] = record
    state["base_review_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    save_run(project, state)
    return record


def single_vendor_only(project: Path, state: dict, context: dict | None = None) -> bool:
    context = context if context is not None else vendor_context(project, state)
    return context["available_vendors"] is not None and len(context["available_vendors"]) == 1


def review_findings_markdown(review: dict) -> list[str]:
    """Show nonblocking agent findings in the human-readable base report."""
    lines = []
    disposition = review.get("finding_disposition") or {}
    for bucket, label in (("legacy_issue", "既有问题"), ("out_of_scope", "范围外")):
        findings = disposition.get(bucket) or []
        if findings:
            lines += ["", f"## {label}", ""]
            lines += [f"- {item.get('message', '')}" for item in findings]
    return lines


def record_human_review(project: Path, change_id: str, verdict: str, note: str,
                        human_review_reason: str | None) -> dict:
    """Record either E1 human review or an explicit E2a single-vendor substitute."""
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
              "candidate_sha256": state["candidate_sha256"]}
    if state.get("implementer", "human") == "agent":
        require(human_review_reason is not None,
                "Agent base review needs --human-review for the single-vendor substitute")
        record.update(human_substitute_fields(project, state, human_review_reason))
        record["candidate_tree_oid"] = _git(project, "rev-parse",
                                             f"{state['candidate_commit']}^{{tree}}").strip()
    else:
        require(human_review_reason is None,
                "E1 human implementation does not use --human-review substitution")
        record["mode"] = "human_e1"
    path = project / ".storyloop/state/base-reviews" / f"{change_id}.json"
    write_json(path, record, max_bytes=1_000_000)
    state["base_review"] = record
    state["base_review_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    save_run(project, state)
    return {"status": "REVIEW_RECORDED", **record}
