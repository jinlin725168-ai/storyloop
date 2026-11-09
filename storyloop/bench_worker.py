"""Run a frozen Case with a selected StoryLoop source tree in a child process."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys


def main(request_path: str, response_path: str) -> None:
    request = json.loads(Path(request_path).read_text())
    sys.path.insert(0, request["engine_tree"])
    from storyloop import __version__
    from storyloop.approvals import approve_change
    from storyloop.changes import create_change, load_change
    from storyloop.design_approval import approve_design
    from storyloop.models import StoryLoopError
    from storyloop.onboarding_cli import bootstrap_with_project_checks
    from storyloop.project_loop import load_run_state, run_change
    from storyloop.readiness import inspect_readiness
    from storyloop.smoke import install_smoke_suite
    from storyloop.baseline import load_project_config
    from storyloop.storage import write_json
    from storyloop.telemetry import ledger_entries

    project = Path(request["project"])
    contract = request["contract"]
    change_id = contract["change_id"]
    outcome = {"status": "NOT_COMPARABLE", "events": [], "engine_version": __version__,
               "calls": None, "agent_ms": None, "known_cost_usd": None, "cost_coverage": None}
    try:
        settings_path = project / "acceptance/storyloop-settings.json"
        if not settings_path.is_file():
            raise StoryLoopError("Frozen repository lacks acceptance/storyloop-settings.json")
        settings = json.loads(settings_path.read_text())
        settings.update(_adapter_settings(request))
        bootstrap_with_project_checks(project, settings)
        config = load_project_config(project)
        config["max_calls"] = request["budget"]["max_calls"]
        config["max_agent_wall_seconds"] = request["budget"]["max_wall_seconds"]
        write_json(project / ".storyloop/config.json", config, max_bytes=512_000)
        ready = inspect_readiness(project)
        if ready["status"] != "READY":
            outcome.update(status="NOT_COMPARABLE", reason=f"readiness:{ready['status']}")
        else:
            outcome.update(_execute_change(
                project, contract, request["case_source"], request["case_version"],
                request.get("suite_source", "generated"), create_change=create_change,
                approve_change=approve_change, load_change=load_change,
                install_smoke_suite=install_smoke_suite, run_change=run_change,
                approve_design=approve_design, load_run_state=load_run_state))
    except (StoryLoopError, OSError, ValueError, KeyError) as exc:
        outcome.update(status="NOT_COMPARABLE", reason=f"engine:{type(exc).__name__}: {exc}")
    try:
        entries = ledger_entries(project, change_id)
        outcome.update(_resources(entries))
        change = load_change(project, change_id)
        outcome["events"] = [*change.get("intervention_events", []), *outcome["events"]]
    except (StoryLoopError, OSError):
        pass
    Path(response_path).write_text(json.dumps(outcome, ensure_ascii=False))


def _adapter_settings(request: dict) -> dict:
    adapter = request["adapter"]
    if adapter == "codex":
        profile = {"kind": "codex", "models": {"strong": request["model"],
                                               "standard": request["model"]}}
    else:
        command = request.get("agent_command")
        if not isinstance(command, list) or "{model}" not in command:
            raise ValueError("Claude --agent-command must contain {model} and use StoryLoop JSON transport")
        profile = {"kind": "command", "agent_command": [
            request["model"] if item == "{model}" else item for item in command]}
    return {"adapters": {"bench": profile}, "roles": {"default_adapter": "bench"}}


def _execute_change(project: Path, contract: dict, source: str, version: int,
                    suite_source: str, *, create_change, approve_change, load_change,
                    install_smoke_suite, run_change, approve_design, load_run_state) -> dict:
    """Follow approve's execution path; only the explicit isolation mode installs a suite."""
    if suite_source not in {"generated", "frozen"}:
        raise ValueError("Unknown bench suite source")
    change_id = contract["change_id"]
    stamp = datetime.strptime(change_id.split("-")[1], "%Y%m%d").replace(tzinfo=timezone.utc)
    slug = "-".join(change_id.split("-")[2:])
    created = create_change(project, "Frozen bench Case", contract["product"],
                            now=lambda: stamp, slug=slug,
                            require_verification_entries=version == 2)
    if created["change_id"] != change_id:
        raise ValueError("Frozen change ID does not match the product contract")
    approve_change(project, change_id, approved_by="bench-frozen-contract")
    if suite_source == "frozen":
        _install_suite(project, source, change_id, load_change, install_smoke_suite)
    return _run_until_stop(project, change_id, run_change, approve_design, load_run_state)


def _install_suite(project: Path, source: str, change_id: str, load_change, install) -> None:
    root = Path(source) / "checks/acceptance/changes" / change_id
    manifest = json.loads((root / "manifest.json").read_text())
    files = [{"path": item.relative_to(root).as_posix(), "content": item.read_text()}
             for item in root.rglob("*") if item.is_file() and item.name != "manifest.json"]
    install(project, load_change(project, change_id), files, manifest)


def _run_until_stop(project, change_id, run_change, approve_design, load_run_state):
    from storyloop.models import StoryLoopError
    for _ in range(3):
        try:
            status = run_change(project, change_id).get("status")
        except StoryLoopError as exc:
            status = exc.code
        if status == "PASSED":
            return {"status": "PASSED", "events": _run_events(project, change_id, load_run_state)}
        if status == "DESIGN_APPROVAL_REQUIRED":
            state = load_run_state(project, change_id)
            plan = (state.get("planning") or {}).get("plan") or {}
            approve_design(project, change_id, plan["id"])
            continue
        if status == "MANUAL_ACCEPTANCE_REQUIRED":
            return {"status": "NOT_COMPARABLE", "reason": "manual_acceptance_has_no_frozen_rule",
                    "events": _run_events(project, change_id, load_run_state)}
        correction = {"NEEDS_INPUT": ("answer", {"planned": False}),
                      "ORACLE_DEFECT": ("acceptance_repair", {}),
                      "EXISTING_GREEN": ("acceptance_repair", {}),
                      "DESIGN_REVISION_REQUIRED": ("replan", {}),
                      "WORKSPACE_TAINTED": ("reset", {}),
                      "NEEDS_SPEC_FIX": ("supersede", {}),
                      "NO_PROGRESS": ("replan", {})}.get(status)
        events = _run_events(project, change_id, load_run_state)
        if correction:
            kind, fields = correction
            return {"status": "NEEDS_HUMAN", "reason": status,
                    "events": [*events, {"kind": kind, **fields}]}
        if status in {"BASE_CHANGE_REQUIRED", "SPLIT_RECOMMENDED", "HUMAN_IMPLEMENTATION_REQUIRED",
                      "BASE_REVIEW_REQUIRED"}:
            return {"status": "NEEDS_HUMAN", "reason": status,
                    "nonroutine_stop": status, "events": events}
        return {"status": status, "events": events}
    return {"status": "NOT_COMPARABLE", "reason": "routine_stop_loop_limit",
            "events": _run_events(project, change_id, load_run_state)}


def _run_events(project, change_id, load_run_state):
    try:
        return load_run_state(project, change_id).get("intervention_events", [])
    except (OSError, ValueError):
        return []


def _resources(entries: list[dict]) -> dict:
    if not entries:
        return {"calls": 0, "agent_ms": 0, "input_tokens": None,
                "output_tokens": None, "known_cost_usd": None, "cost_coverage": None}
    agent_ms = sum(item["wall_ms"] for item in entries if isinstance(item.get("wall_ms"), int))
    costs = [item["cost_usd"] for item in entries if isinstance(item.get("cost_usd"), (int, float))]
    tokens = {key: sum(item[key] for item in entries) if all(
        type(item.get(key)) is int for item in entries) else None
        for key in ("input_tokens", "output_tokens")}
    return {"calls": len(entries), "agent_ms": agent_ms if len(entries) == sum(
                isinstance(item.get("wall_ms"), int) for item in entries) else None,
            "known_cost_usd": round(sum(costs), 4) if costs else None,
            "cost_coverage": len(costs) / len(entries), **tokens}


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
