"""`storyloop log`: one change's history in one place, read from state the engine already keeps.

Agent calls come from the telemetry ledger joined with their diagnostic records (outcome, error,
schema problems, session ids, folder), attempt failures from the run state, and the closing line
is the next command. Nothing here is written; it is a view for diagnosis.
"""

from __future__ import annotations

from pathlib import Path

from .call_log import call_folder, load_meta
from .changes import load_change
from .next_steps import next_step
from .storage import load_json
from .telemetry import ledger_entries, summarize

LAYER_NAMES = ("unit", "integration", "acceptance", "smoke", "checks")


def change_log(project: Path, change_id: str) -> dict:
    project = Path(project).resolve()
    change = load_change(project, change_id)
    run_path = project / ".storyloop/state/runs" / f"{change_id}.json"
    run = load_json(run_path) if run_path.exists() else None
    status = (run or {}).get("status") or change["status"]
    return {
        "status": "LOG", "change_id": change_id, "change_status": change["status"],
        "run_status": run.get("status") if run else None,
        "attempt": run.get("attempt") if run else None,
        "calls": [_call_view(project, entry) for entry in ledger_entries(project, change_id)],
        "failures": [_failure_view(item) for item in (run or {}).get("failures", [])],
        "error": (run or {}).get("error"),
        "telemetry": summarize(project, change_id),
        "next": next_step(status, change_id, project, run or {}),
    }


def _call_view(project: Path, entry: dict) -> dict:
    view = {key: entry[key] for key in ("at", "role", "wall_ms", "cost_usd", "turns", "failed", "session")
            if key in entry}
    call = entry.get("call")
    if call:
        meta = load_meta(project, call) or {}
        view["call"] = call
        view["folder"] = str(call_folder(project, call))
        for key in ("status", "code", "error", "schema_errors", "repairs"):
            if meta.get(key):
                view[key] = meta[key]
    return view


def _failure_view(failure: dict) -> dict:
    """One line per failed attempt: which gate said no and the gist of why."""
    attempt = failure.get("attempt")
    if "contract" in failure:
        return {"attempt": attempt, "gate": "contract", "reason": failure["contract"].get("message", "")}
    if "review" in failure:
        review = failure["review"] or {}
        findings = review.get("findings") or []
        first = findings[0].get("message", "") if findings and isinstance(findings[0], dict) else ""
        return {"attempt": attempt, "gate": "review",
                "reason": f"{len(findings)} finding(s): {first or review.get('summary', '')}"[:300]}
    if "characterization" in failure:
        paths = failure["characterization"].get("uncharacterized") or []
        return {"attempt": attempt, "gate": "characterization",
                "reason": "modified without a lock, restored and locked: " + ", ".join(paths[:5])}
    red = [name for name in LAYER_NAMES
           if isinstance(failure.get(name), dict) and failure[name].get("status") not in {"PASSED", "SKIPPED"}]
    if red:
        return {"attempt": attempt, "gate": "layers",
                "reason": ", ".join(f"{name}={failure[name].get('status')}" for name in red)}
    return {"attempt": attempt, "gate": "unknown", "reason": ""}


def render_log(result: dict) -> str:
    """The human form of change_log()."""
    run = result.get("run_status") or "not started"
    lines = [f"{result['change_id']}  合同 {result['change_status']}  运行 {run}"
             + (f"  第 {result['attempt']} 轮" if result.get("attempt") else "")]
    if result.get("error"):
        lines.append(f"  错误: {result['error']}")
    lines.append("")
    lines.append("代理调用：")
    for call in result["calls"] or []:
        cost = f" ${call['cost_usd']:.2f}" if isinstance(call.get("cost_usd"), (int, float)) else ""
        state = call.get("status") or ("FAILED" if call.get("failed") else "OK")
        lines.append(f"  {call.get('at', '?')}  {call['role']:<12} {state:<7} "
                     f"{call.get('wall_ms', 0) / 1000:>7.1f}s{cost}"
                     + (f"  会话 {call['session']}" if call.get("session") else ""))
        if call.get("repairs"):
            lines.append(f"      修复重试 {call['repairs']} 次")
        for problem in call.get("schema_errors") or []:
            lines.append(f"      结构问题: {problem}")
        if call.get("error"):
            lines.append(f"      错误: {call['error'][:300]}")
        if call.get("folder"):
            lines.append(f"      记录: {call['folder']}")
    if not result["calls"]:
        lines.append("  （无）")
    if result["failures"]:
        lines.append("")
        lines.append("失败的尝试：")
        for failure in result["failures"]:
            lines.append(f"  第 {failure['attempt']} 轮  {failure['gate']}: {failure['reason']}")
    total = result.get("telemetry") or {}
    cost = f"，费用 ${total['cost_usd']:.2f}" if isinstance(total.get("cost_usd"), (int, float)) else ""
    lines.append("")
    lines.append(f"合计 {total.get('calls', 0)} 次调用，代理耗时 {total.get('agent_ms', 0) / 60000:.1f} 分钟{cost}")
    if result.get("next"):
        lines.append(f"下一步: {result['next']}")
    return "\n".join(lines)
