"""Independent vendor review or a named single-vendor human substitute."""

from __future__ import annotations

from pathlib import Path
import hashlib

from .baseline import _git, load_project_config
from .call_context import capability_for
from .guarded_calls import read_only_call
from .models import require, text
from .project_agents import invoke_project_agent
from .project_receipt import _dirty_paths
from .role_profiles import enabled, resolve_adapters, role_settings
from .run_guards import base_candidate_authors, trusted_base_evidence
from .run_state import run_path, save_run
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
    require(enabled(config), "Agent base review needs configured adapters")
    adapters = resolve_adapters(config)
    implement_adapter = state.get("agent_adapter")
    review_adapter = role_settings(config, "review", "heavy", base=True)["adapter"]
    vendors = {name: capability_for(project, name).get("vendor") for name in adapters}
    known = (set(value.strip().casefold() for value in vendors.values()
                 if isinstance(value, str) and value.strip())
             if all(isinstance(value, str) and value.strip() for value in vendors.values())
             else None)
    implement_vendor = vendors.get(implement_adapter)
    require(implement_vendor == state.get("agent_vendor") and implement_vendor,
            "Base implement vendor handshake changed after candidate creation")
    return {"implement_vendor": implement_vendor, "review_vendor": vendors.get(review_adapter),
            "review_adapter": review_adapter, "available_vendors": known}


def human_substitute_fields(project: Path, state: dict, reason: str) -> dict:
    """Permit named human review only if one known vendor can actually be configured."""
    context = vendor_context(project, state)
    require(context["available_vendors"] is not None
            and len(context["available_vendors"]) == 1,
            "Human review substitute requires exactly one proven adapter vendor")
    cleaned = " ".join(reason.split()) if isinstance(reason, str) else ""
    require(len(cleaned) >= 12, "Human review substitute reason needs at least 12 characters")
    return {"mode": "human_substitute", "human_review_reason": cleaned,
            "implement_vendor": context["implement_vendor"],
            "plan_id": state["base_design"]["plan_id"],
            "design_sha256": state["base_design_sha256"],
            "contract_digest": state["contract_digest"]}


def review_agent_candidate(project: Path, change: dict, state: dict, services=None) -> dict:
    """Call the distinct reviewer read-only and bind its verdict to this attempt."""
    context = vendor_context(project, state)
    implement_vendor, review_vendor = require_distinct_vendors(
        context["implement_vendor"], context["review_vendor"])
    payload = {"mode": "base-review", "change": change,
               "candidate_commit": state["candidate_commit"],
               "candidate_sha256": state["candidate_sha256"],
               "design_plan": state["base_design"]}
    operation = (lambda: services.review(project, payload)) if services else (
        lambda: invoke_project_agent(project, "review", payload))
    answer = read_only_call(project, change["change_id"], operation)
    require(isinstance(answer, dict) and answer.get("verdict") == "pass"
            and not answer.get("questions"), "Independent base agent review did not pass")
    record = {"version": 1, "change_id": change["change_id"], "verdict": "pass",
              "note": answer.get("summary", "independent agent review"),
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


def single_vendor_only(project: Path, state: dict) -> bool:
    context = vendor_context(project, state)
    return context["available_vendors"] is not None and len(context["available_vendors"]) == 1


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
