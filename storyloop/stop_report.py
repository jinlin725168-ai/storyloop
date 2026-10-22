"""Build and render the evidence-backed report shared by every explicit stop."""

from __future__ import annotations

from pathlib import Path
import shlex
from .cost_limits import stop_fields, render_usage
from .routing_report import STATUSES, routing_options, routing_view


LAYER_NAMES = ("unit", "integration", "acceptance", "smoke", "checks")
PHASES = frozenset({"contract", "prepare", "planning", "implement", "verify", "finish"})
from .run_state import DISPLAY_STOPS as STOP_STATUSES

BLOCKERS = {
    "NEEDS_INPUT": "代理提出了需要人决定的问题",
    "ORACLE_DEFECT": "验收用例本身有缺陷，实现无法让它正确地通过",
    "EXISTING_GREEN": "验收用例在实现前就已通过，证明不了 RED",
    "CHARACTERIZATION_FAILED": "特征化测试在旧代码上不绿",
    "BOUNDARY_VIOLATION": "代理写了不允许的路径",
    "BASE_CHANGE_REQUIRED": "改动碰到了底座",
    "WORKSPACE_TAINTED": "工作区出现了非预期的改动",
    "COST_LIMIT_REACHED": "变更的代理调用达到成本上限",
    "BUDGET_EXHAUSTED": "实现轮次用完了",
    "NO_PROGRESS": "根因调查后失败特征仍不变",
    "MANUAL_ACCEPTANCE_REQUIRED": "有验收条件需要人工核对",
    "NEEDS_SPEC_FIX": "合同按现在的写法做不出来",
    "AGENT_UNAVAILABLE": "代理进程没有给出回答",
}


def _command(*tokens: object) -> str:
    """Return a copy-pasteable command, including safe literal placeholders."""
    return shlex.join(str(token) for token in tokens)


def _options(status: str, project: str, change_id: str, routed: bool) -> list[dict]:
    where = (project, "--change", change_id)
    repair = {"action": "修正判据", "command": _command(
        "storyloop", "acceptance-repair", *where, "--reason", "<原因>")}
    supersede = {"action": "作废并修订重开", "command": _command(
        "storyloop", "change", project, "--supersede", change_id,
        "--message", "<修订意见>")}
    supersede_with_reason = {"action": "作废并修订重开", "command": _command(
        "storyloop", "change", project, "--supersede", change_id,
        "--message", "<修订意见>", "--reason", "<为什么是合同的问题>")}
    overrule = {"action": "驳回归属", "command": _command(
        "storyloop", "run", *where, "--overrule", "<理由>")}
    run = {"action": "处理后重试", "command": _command("storyloop", "run", *where)}
    table = {
        "NEEDS_INPUT": [{"action": "回答", "command": _command(
            "storyloop", "answer", *where, "--answer", "<回答>")}],
        "ORACLE_DEFECT": [repair, *([overrule] if routed else [])],
        "EXISTING_GREEN": [repair],
        "CHARACTERIZATION_FAILED": [run],
        "BOUNDARY_VIOLATION": [
            {"action": "还原越界文件后继续", "command": _command("storyloop", "run", *where)},
            {"action": "确需扩大范围时发起底座提案", "command": _command(
                "storyloop", "base-propose", project, "--path", "<路径>", "--reason", "<原因>")},
        ],
        "BASE_CHANGE_REQUIRED": [{"action": "升级底座", "command": _command(
            "storyloop", "base-update", project, "--proposal", "<BASE-ID>")}],
        "WORKSPACE_TAINTED": [{"action": "清理后复位", "command": _command(
            "storyloop", "reset", *where, "--reason", "<原因>")}],
        "COST_LIMIT_REACHED": [{**run, "action": "提高 .storyloop/config.json 的 limits 后继续同一轮"}, supersede],
        "BUDGET_EXHAUSTED": [repair, supersede_with_reason, run],
        "NO_PROGRESS": [repair, supersede_with_reason, run],
        "MANUAL_ACCEPTANCE_REQUIRED": [{"action": "逐条人工验收", "command": _command(
            "storyloop", "accept", *where, "--criterion", "<ID>", "--note", "<核对内容>")}],
        "NEEDS_SPEC_FIX": [supersede, *([overrule] if routed else [])],
        "AGENT_UNAVAILABLE": [run],
    }
    return table.get(status, [])


def failure_view(failure: dict) -> dict:
    """Return the established compact view of an old or new attempt failure."""
    attempt = failure.get("attempt")
    red = [name for name in LAYER_NAMES
           if isinstance(failure.get(name), dict)
           and failure[name].get("status") not in {"PASSED", "SKIPPED"}]
    if "contract" in failure:
        return {"attempt": attempt, "gate": "contract", "reason": failure["contract"].get("message", "")}
    review = failure.get("review") or {}
    if "review" in failure and (review.get("findings") or review.get("verdict") == "needs_input" or not red):
        findings = review.get("findings") or []
        first = findings[0].get("message", "") if findings and isinstance(findings[0], dict) else ""
        reason = f"{len(findings)} finding(s): {first or review.get('summary', '')}"[:300]
        return {"attempt": attempt, "gate": "review", "reason": reason}
    if "boundary" in failure:
        boundary = failure["boundary"] or {}
        violations = boundary.get("violations") or []
        paths = [f"{item.get('category', 'violation')}: {item.get('path', '')}"
                 for item in violations[:5] if isinstance(item, dict)]
        reason = "; ".join([boundary.get("status", "boundary violation"), *paths])[:300]
        return {"attempt": attempt, "gate": "boundary", "reason": reason}
    if "characterization" in failure:
        paths = failure["characterization"].get("uncharacterized") or []
        reason = "modified without a lock, restored and locked: " + ", ".join(paths[:5])
        return {"attempt": attempt, "gate": "characterization", "reason": reason}
    if red:
        reason = ", ".join(f"{name}={failure[name].get('status')}" for name in red)
        return {"attempt": attempt, "gate": "layers", "reason": reason}
    return {"attempt": attempt, "gate": "unknown", "reason": ""}


def _completed(state: dict) -> list[str]:
    completed = []
    if state.get("contract_commit"):
        completed.append("contract")
    if state.get("red_evidence_sha256"):
        completed.append("acceptance_red")
    if (state.get("design_plan") or {}).get("status") in {"DONE", "SKIPPED"}:
        completed.append("design")
    if (state.get("characterization") or {}).get("status") in {"GREEN", "SKIPPED"}:
        completed.append("characterization")
    for failure in state.get("failures") or []:
        view = failure_view(failure)
        if failure.get("attempt") is not None:
            completed.append(f"implement:{failure['attempt']}")
            if view["gate"] != "unknown":
                completed.append(f"{view['gate']}:{failure['attempt']}")
    return list(dict.fromkeys(completed))


def _phase(state: dict, status: str, details: dict) -> str:
    explicit = details.get("phase") or state.get("phase")
    if explicit == "planning":
        return (state.get("planning") or {}).get("phase") or explicit
    if explicit in PHASES:
        # Planning checkpoints remain as history after execution resumes.
        return explicit
    routed = state.get("routed") or {}
    if routed.get("source") == "review" or status in {"MANUAL_ACCEPTANCE_REQUIRED", "NEEDS_SPEC_FIX"}:
        return "verify"
    if status in {"ORACLE_DEFECT", "EXISTING_GREEN", "CHARACTERIZATION_FAILED"}:
        return "prepare"
    if not state.get("contract_commit"):
        return "contract"
    design = state.get("design_plan") or {}
    if not state.get("red_evidence_sha256") or design.get("status") not in {"DONE", "SKIPPED"}:
        return "prepare"
    return "implement"


def _review_verdict(state: dict) -> dict | None:
    routed = state.get("routed") or {}
    if routed.get("source") != "review":
        return None
    findings = routed.get("findings") or []
    messages = [item.get("message", "") for item in findings if isinstance(item, dict)]
    summary = next((message for message in messages if message), routed.get("summary", ""))
    return {"gate": "review", "summary": summary}


def _last_verdict(state: dict, details: dict) -> dict | None:
    explicit = details.get("last_verdict") or state.get("last_verdict")
    if isinstance(explicit, dict):
        return explicit
    review = _review_verdict(state)
    if review:
        return review
    failures = state.get("failures") or []
    if not failures:
        return None
    view = failure_view(failures[-1])
    return {"gate": view["gate"], "summary": view["reason"]}


def _evidence(state: dict, details: dict) -> list[str]:
    routed = (state.get("routed") or {}).get("findings") or []
    evidence = [item["location"] for item in routed if isinstance(item, dict) and item.get("location")]
    violations = details.get("violations") or state.get("violations") or []
    evidence += [item.get("path", "") for item in violations if isinstance(item, dict)]
    acceptance = details.get("acceptance") or state.get("acceptance") or {}
    for key in ("criteria_failing", "criteria_green"):
        evidence += [f"条件 {criterion}" for criterion in acceptance.get(key) or []]
    for item in details.get("pending") or state.get("pending") or []:
        criterion = item.get("criterion_id") or item.get("id") if isinstance(item, dict) else item
        evidence.append(f"条件 {criterion}")
    for failure in details.get("agent_failures") or state.get("agent_failures") or []:
        if not isinstance(failure, dict):
            continue
        parts = [failure.get("code", "AGENT_UNAVAILABLE")]
        parts += [f"role={failure['role']}"] if failure.get("role") else []
        parts += [f"attempt={failure['attempt']}"] if failure.get("attempt") is not None else []
        parts += [f"call={failure['call']}"] if failure.get("call") else []
        message = failure.get("message") or failure.get("error")
        parts += [message] if message else []
        evidence.append("；".join(parts))
    return [item for item in dict.fromkeys(evidence) if item]


def build_stop(project: Path | str, state: dict, status: str, details: dict) -> dict:
    """Construct the stable report shape without changing the supplied run state."""
    project_text = str(project)
    change_id = str(state["change_id"])
    failures = state.get("failures") or []
    routed_options = routing_options(project, change_id, status, {**state, **details})
    options = routed_options or _options(status, project_text, change_id, bool(state.get("routed")))
    return {
        **stop_fields(project, change_id, details),
        "status": status,
        "routing": routing_view(state),
        "phase": _phase(state, status, details),
        "completed": _completed(state),
        "last_verdict": _last_verdict(state, details),
        "attempts": [{
            "attempt": item.get("attempt"),
            "attribution": item.get("attribution", "implement"),
            "signature": (item.get("signature") or "")[:8],
            "summary": failure_view(item)["reason"],
        } for item in failures],
        "blocker": details.get("error") or state.get("error") or BLOCKERS.get(status, status),
        "evidence": _evidence(state, details),
        "facts": list(details.get("facts") or state.get("facts") or []),
        "questions": list(details.get("questions") or state.get("questions") or []),
        "options": options,
        "resume": (options[0]["command"] if status == "NEEDS_SPEC_FIX" or routed_options else
                   _command("storyloop", "run", project_text, "--change", change_id)),
    }


def stop_of(project: Path | str, run: dict | None) -> dict | None:
    """Return a saved report, or synthesize one for a legacy explicit-stop run."""
    if not run:
        return None
    saved = run.get("stop")
    if isinstance(saved, dict) and saved.get("status") == "AGENT_UNAVAILABLE":
        return saved
    if run.get("status") not in STOP_STATUSES:
        return None
    if isinstance(saved, dict) and saved.get("status") == run["status"]:
        return saved
    return build_stop(project, run, run["status"], {})


def render_stop(stop: dict) -> list[str]:
    """Render the report in the decision order defined by the design."""
    lines = [f"阻塞点：{stop['blocker']}"] + render_usage(stop)
    for key, heading in (("evidence", "证据"), ("facts", "代理陈述事实"), ("questions", "问题")):
        if stop.get(key):
            lines.append(f"{heading}：")
            lines += [f"  - {_render_item(item)}" for item in stop[key]]
    if stop.get("options"):
        lines.append("可选操作：")
        lines += [f"  {number}. {option['action']}：{option['command']}"
                  for number, option in enumerate(stop["options"], 1)]
    lines.append(f"恢复：{stop['resume']}")
    return lines


def _render_item(item: object) -> str:
    if isinstance(item, dict):
        return str(item.get("statement") or item.get("question") or item.get("message") or item)
    return str(item)


def build_entry_stop(project: Path, status: str, details: dict) -> dict:
    """Report a blocked entry without fabricating a run or implementation attempts."""
    options = routing_options(project, details.get('change_id'), status, details)
    return {'status': status, 'phase': 'intake', 'blocker': details.get('error', status),
            'evidence': details.get('evidence', details.get('layers', {})),
            'details': details, 'options': options,
            'resume': options[0]['command'] if options else _command('storyloop', 'readiness', project)}
