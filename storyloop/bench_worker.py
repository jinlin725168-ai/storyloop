"""Run a frozen Case with a selected StoryLoop source tree in a child process."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

NONROUTINE_BUDGET = 1
STOP_REASON = "bench simulated human: apply engine stop evidence"
RECOVERABLE = {"ORACLE_DEFECT": "acceptance_repair", "EXISTING_GREEN": "acceptance_repair",
               "DESIGN_REVISION_REQUIRED": "replan", "NO_PROGRESS": "replan",
               "WORKSPACE_TAINTED": "reset"}


def main(request_path: str, response_path: str) -> None:
    request = json.loads(Path(request_path).read_text())
    sys.path.insert(0, request["engine_tree"])
    sys.path.extend(request.get("engine_sites", []))
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
            outcome.update(_readiness_failure(project, ready))
        else:
            outcome.update(_execute_change(
                project, contract, request["case_source"], request["case_version"],
                request.get("suite_source", "generated"), create_change=create_change,
                approve_change=approve_change, load_change=load_change,
                install_smoke_suite=install_smoke_suite, run_change=run_change,
                approve_design=approve_design, load_run_state=load_run_state,
                stop_capture_root=Path(response_path).parent / "stop-evidence"))
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


def _readiness_failure(project: Path, ready: dict) -> dict:
    from storyloop.processutil import redact
    layers = {}
    for name, layer in ready.get("layers", {}).items():
        failed = []
        for result in layer.get("results", []):
            if result.get("status") == "PASS":
                continue
            tail = result.get("output_tail") or {}
            failed.append({"id": result.get("id"), "returncode": result.get("returncode"),
                           "timed_out": result.get("timed_out"),
                           "output_tail": {key: redact(str(tail.get(key, "")))[-2000:]
                                           for key in ("stdout", "stderr")}})
        layers[name] = {"status": layer.get("status"), "failed_results": failed}
    summary = {"status": ready["status"], "blockers": ready.get("blockers", []),
               "layers": layers}
    paths = {layer.get("evidence_path") for layer in ready.get("layers", {}).values()
             if layer.get("evidence_path")}
    result = {"status": "NOT_COMPARABLE", "reason": f"readiness:{ready['status']}",
              "readiness": summary}
    if len(paths) == 1:
        result["readiness_evidence_source"] = paths.pop()
    return result


def _execute_change(project: Path, contract: dict, source: str, version: int,
                    suite_source: str, *, create_change, approve_change, load_change,
                    install_smoke_suite, run_change, approve_design, load_run_state,
                    stop_capture_root: Path | None = None) -> dict:
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
    return _run_until_stop(project, change_id, run_change, approve_design, load_run_state,
                           stop_capture_root=stop_capture_root)


def _install_suite(project: Path, source: str, change_id: str, load_change, install) -> None:
    root = Path(source) / "checks/acceptance/changes" / change_id
    manifest = json.loads((root / "manifest.json").read_text())
    files = [{"path": item.relative_to(root).as_posix(), "content": item.read_text()}
             for item in root.rglob("*") if item.is_file() and item.name != "manifest.json"]
    install(project, load_change(project, change_id), files, manifest)


def _run_until_stop(project, change_id, run_change, approve_design, load_run_state,
                    *, repair_smoke=None, replan=None, reset_run=None,
                    stop_capture_root: Path | None = None):
    from storyloop.models import StoryLoopError
    simulated, stops, evidence = [], [], []
    for _ in range(6):
        try:
            result = run_change(project, change_id)
            status = result.get("status")
        except StoryLoopError as exc:
            result, status = {"error": str(exc)}, exc.code
        state = _safe_run_state(project, change_id, load_run_state)
        status = state.get("status") or status
        if status == "PASSED":
            return _finish_stop(project, change_id, load_run_state, "PASSED", stops,
                                simulated, evidence)
        stops.append({"status": status, "detail": _stop_detail(state, result)})
        if stop_capture_root is not None:
            evidence.extend(_capture_stop_files(project, change_id, stop_capture_root, len(stops)))
        if status == "DESIGN_APPROVAL_REQUIRED":
            approve_design(project, change_id, state["planning"]["plan"]["id"])
            continue
        if status in RECOVERABLE and len(simulated) < NONROUTINE_BUDGET:
            try:
                _recover(project, change_id, status, repair_smoke, replan, reset_run)
            except StoryLoopError as exc:
                stops[-1]["detail"]["recovery_error"] = _bound(str(exc))
                return _finish_stop(project, change_id, load_run_state, "NEEDS_HUMAN",
                                    stops, simulated, evidence, reason=status,
                                    extra={"kind": RECOVERABLE[status], "stop": status})
            simulated.append({"kind": RECOVERABLE[status], "simulated": True, "stop": status})
            continue
        return _terminal_stop(project, change_id, load_run_state, status, stops, simulated, evidence)
    return _finish_stop(project, change_id, load_run_state, "NOT_COMPARABLE", stops,
                        simulated, evidence, reason="routine_stop_loop_limit")


def _recover(project, change_id, status, repair_smoke, replan, reset_run):
    if status in {"ORACLE_DEFECT", "EXISTING_GREEN"}:
        if repair_smoke is None:
            from storyloop.acceptance_repair import repair_smoke
        return repair_smoke(project, change_id, STOP_REASON)
    if status in {"DESIGN_REVISION_REQUIRED", "NO_PROGRESS"}:
        if replan is None:
            from storyloop.design_approval import replan
        return replan(project, change_id, STOP_REASON)
    if reset_run is None:
        from storyloop.run_recovery import reset_run
    return reset_run(project, change_id, STOP_REASON)


def _terminal_stop(project, change_id, load_run_state, status, stops, simulated, evidence):
    if status == "MANUAL_ACCEPTANCE_REQUIRED":
        return _finish_stop(project, change_id, load_run_state, "NOT_COMPARABLE", stops,
                            simulated, evidence, reason="manual_acceptance_has_no_frozen_rule")
    correction = {"NEEDS_INPUT": {"kind": "answer", "planned": False},
                  "NEEDS_SPEC_FIX": {"kind": "supersede", "stop": status}}
    if status in RECOVERABLE:
        correction[status] = {"kind": RECOVERABLE[status], "stop": status}
    if status in correction:
        return _finish_stop(project, change_id, load_run_state, "NEEDS_HUMAN", stops,
                            simulated, evidence, reason=status, extra=correction[status])
    if status in {"BASE_CHANGE_REQUIRED", "SPLIT_RECOMMENDED", "HUMAN_IMPLEMENTATION_REQUIRED",
                  "BASE_REVIEW_REQUIRED"}:
        return _finish_stop(project, change_id, load_run_state, "NEEDS_HUMAN", stops,
                            simulated, evidence, reason=status, nonroutine_stop=status)
    return _finish_stop(project, change_id, load_run_state, status, stops, simulated, evidence)


def _finish_stop(project, change_id, load_run_state, status, stops, simulated, evidence,
                 *, reason=None, extra=None, nonroutine_stop=None):
    events = list(_run_events(project, change_id, load_run_state))
    for marker in simulated:
        for index in range(len(events) - 1, -1, -1):
            event = events[index]
            if (event.get("kind") == marker["kind"] and event.get("stop") == marker["stop"]
                    and event.get("automatic") is not True and event.get("simulated") is not True):
                events[index] = {**event, **marker}
                break
        else:
            events.append(marker)
    if extra is not None:
        events.append(extra)
    result = {"status": status, "events": events, "stops": stops,
              "stop_evidence_sources": evidence}
    if reason is not None:
        result["reason"] = reason
    if nonroutine_stop is not None:
        result["nonroutine_stop"] = nonroutine_stop
    return result


def _safe_run_state(project, change_id, load_run_state) -> dict:
    from storyloop.models import StoryLoopError
    try:
        return load_run_state(project, change_id)
    except (StoryLoopError, OSError, ValueError):
        return {}


def _bound(value, depth: int = 0):
    from storyloop.processutil import redact
    if isinstance(value, str):
        return redact(value)[:2000]
    if depth >= 5:
        return "[TRUNCATED]"
    if isinstance(value, list):
        return [_bound(item, depth + 1) for item in value[:20]]
    if isinstance(value, dict):
        return {str(key): _bound(item, depth + 1) for key, item in list(value.items())[:30]}
    return value


def _redact_json(value):
    from storyloop.processutil import redact
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if any(word in str(key).lower()
                                            for word in ("password", "token", "secret", "credential",
                                                         "api_key", "authorization"))
                      else _redact_json(item)) for key, item in value.items()}
    return value


def _stop_detail(state: dict, result: dict) -> dict:
    stop = state.get("stop") or result.get("stop") or {}
    failures = state.get("failures") or []
    return _bound({"reason": state.get("error") or stop.get("blocker") or result.get("error"),
                   "stop": stop, "acceptance": state.get("acceptance"),
                   "failure": failures[-1] if failures else None})


def _capture_stop_files(project: Path, change_id: str, root: Path, number: int) -> list[str]:
    sources = {"run-state.json": Path(".storyloop/state/runs") / f"{change_id}.json",
               "acceptance-red.json": Path(".storyloop/evidence") / change_id / "acceptance-red.json",
               "acceptance-result.json": Path(".storyloop/evidence") / change_id / "acceptance-result.json"}
    captured = []
    for name, relative in sources.items():
        source = project / relative
        if not source.exists() or source.stat().st_size > 2_000_000:
            continue
        target = Path(f"stop-{number}") / name
        _stage_stop_json(project, relative, root, target)
        captured.append((Path("stop-evidence") / target).as_posix())
    return captured


def _stage_stop_json(source_root: Path, source: Path, target_root: Path, target: Path) -> None:
    from storyloop.models import require
    for relative in (source, target):
        require(not relative.is_absolute() and ".." not in relative.parts,
                "Invalid bench evidence path")
    original = source_root / source
    require(original.resolve().is_relative_to(source_root.resolve())
            and not any(part.is_symlink() for part in (original, *original.parents)
                        if part != source_root.parent)
            and original.is_file() and original.stat().st_size <= 2_000_000,
            "Bench evidence is unavailable")
    destination = target_root / target
    require(destination.resolve().is_relative_to(target_root.resolve()),
            "Bench evidence target is outside output root")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(_redact_json(json.loads(original.read_text())), ensure_ascii=False))


def _run_events(project, change_id, load_run_state):
    return _safe_run_state(project, change_id, load_run_state).get("intervention_events", [])


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
