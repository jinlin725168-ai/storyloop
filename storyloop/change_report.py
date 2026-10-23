"""Requirement-linked product acceptance reports."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from .baseline import _git
from .routing_report import report_routing, routing_markdown
from .changes import load_change
from .models import require
from .report_markdown import render_presentation
from .report_presentation import build_presentation
from .smoke import RESULT_EVIDENCE, load_smoke_manifest, smoke_digests
from .smoke_runner import candidate_digest
from .storage import load_json, write_bytes_atomic, write_json, write_text_atomic

COMMIT = re.compile(r"[0-9a-fA-F]{40,64}")


LAYERS = ("unit", "integration", "acceptance", "smoke", "checks")


def generate_change_report(
    project: Path,
    change_id: str,
    implementation_commit: str,
    layers: dict,
    review: dict,
    manual_acceptance: list[dict] | None = None,
    telemetry: dict | None = None,
    oracle: dict | None = None,
    skills_used: dict | None = None,
    correction: dict | None = None,
) -> Path:
    """Write a passing report only when every layer is green and every criterion has real evidence."""
    project = Path(project).resolve()
    change = load_change(project, change_id)
    evidence_digest = _validate_gate(project, change, implementation_commit, layers, review)
    manifest = load_smoke_manifest(project, change)
    traceability = _build_traceability(
        change, manifest, layers["acceptance"].get("results", []), evidence_digest, manual_acceptance or []
    )
    report = {
        "version": 1,
        "routing": report_routing(project, change),
        "status": "PASSED",
        "change_id": change_id,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "requirement": {
            "goal": change["product"]["goal"],
            "approval": change["approval"],
            "revision": change["revision"],
        },
        "implementation_commit": implementation_commit,
        "layers": layers,
        "review": review,
        "traceability": traceability,
        "telemetry": _telemetry_view(telemetry),
        "oracle": _oracle_view(layers, oracle, skills_used),
        "correction": correction or {"failures": [], "overrules": [], "supersedes": None},
    }
    report["presentation"] = build_presentation(project, change, report)
    root = project / "reports" / "storyloop" / change_id
    path = root / "report.json"
    write_json(path, report, max_bytes=8_000_000)
    _write_markdown(path.with_suffix(".md"), report)
    return path


def record_merge_commit(
    report_path: Path, merge_commit: str, source_report: dict | None = None,
) -> Path:
    """Add the final no-ff merge identity to the persistent acceptance report."""
    report_path = Path(report_path).resolve()
    report = source_report if source_report is not None else load_json(report_path)
    json_bytes, markdown_bytes = merge_receipt_bytes(report, merge_commit)
    require(len(json_bytes) <= 8_000_000, "Acceptance report exceeds size limit")
    write_bytes_atomic(report_path, json_bytes)
    write_bytes_atomic(report_path.with_suffix(".md"), markdown_bytes)
    return report_path


def merge_receipt_bytes(report: dict, merge_commit: str) -> tuple[bytes, bytes]:
    """Render the sole valid post-merge report form from pre-merge authority."""
    require(isinstance(report, dict) and report.get("status") == "PASSED"
            and COMMIT.fullmatch(merge_commit), "Cannot attach an invalid merge receipt")
    require(report.get("merge_commit") in {None, merge_commit},
            "Acceptance report has another merge commit")
    updated = dict(report)
    updated["merge_commit"] = merge_commit
    encoded = (json.dumps(updated, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return encoded, _render_markdown(updated).encode("utf-8")


def _validate_gate(
    project: Path, change: dict, implementation_commit: str, layers: dict, review: dict,
) -> str:
    require(isinstance(layers, dict) and set(layers) == set(LAYERS), "Report needs every verification layer")
    smoke = layers["acceptance"]
    require(change["approval"], "Acceptance report requires an approved contract")
    require(isinstance(implementation_commit, str)
            and COMMIT.fullmatch(implementation_commit) is not None,
            "Acceptance report requires a valid implementation commit")
    require(_git(project, "rev-parse", "HEAD").strip() == implementation_commit,
            "Implementation commit is not the tested current candidate")
    for name in ("unit", "integration", "smoke", "checks"):
        require(layers[name].get("status") in {"PASSED", "SKIPPED"}, f"{name} layer did not pass")
    require(smoke.get("status") == "PASSED", "Change acceptance did not pass")
    require(review.get("verdict") == "pass"
            and not review.get("findings") and not review.get("questions"),
            "Independent review did not pass")
    evidence_path = project / ".storyloop/evidence" / change["change_id"] / RESULT_EVIDENCE
    evidence = load_json(evidence_path)
    evidence_digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    # `uncounted` / `oracle_problems` are the loop's annotations derived from these same results
    # (oracle_counts.check_layers), not part of the evidence file.
    supplied = {key: value for key, value in smoke.items()
                if key not in {"evidence_path", "evidence_sha256", "uncounted", "oracle_problems"}}
    require(smoke.get("evidence_sha256") == evidence_digest and supplied == evidence,
            "Smoke evidence does not match the protected evidence file")
    require(evidence.get("suite_digests") == smoke_digests(project, change),
            "Smoke evidence is not bound to the protected test bytes")
    require(evidence.get("workspace_sha256") == candidate_digest(project),
            "Smoke evidence is not bound to the implementation workspace")
    return evidence_digest


def _build_traceability(
    change: dict, manifest: dict, smoke_results: list, evidence_digest: str,
    manual_acceptance: list[dict],
) -> list[dict]:
    results = {item.get("id"): item for item in smoke_results}
    accepted = {record["criterion_id"]: record for record in manual_acceptance}
    traceability = []
    for story in change["product"]["stories"]:
        story_view = {key: story[key] for key in ("id", "title", "as_a", "want", "so_that")}
        for criterion in story["acceptance"]:
            if criterion["kind"] == "manual":
                record = accepted.get(criterion["id"])
                require(record is not None,
                        "Manual acceptance needs explicit human evidence before a pass report")
                traceability.append({
                    "story": story_view, "criterion": dict(criterion),
                    "tests": [_human_evidence(record)], "evidence_sha256": evidence_digest,
                })
                continue
            declared = [item for item in manifest["tests"] if criterion["id"] in item["criteria"]]
            require(bool(declared), "Automated acceptance criterion has no mapped test")
            tests = [_test_evidence(test, results.get(test["id"])) for test in declared]
            traceability.append({
                "story": story_view,
                "criterion": dict(criterion),
                "tests": tests,
                "evidence_sha256": evidence_digest,
            })
    require(bool(traceability), "Acceptance report needs traceable acceptance criteria")
    return traceability


def _human_evidence(record: dict) -> dict:
    require(isinstance(record.get("note"), str) and record["note"].strip()
            and isinstance(record.get("accepted_at"), str),
            "Manual acceptance record is incomplete")
    return {
        "id": "human", "command": ["human"], "timeout_seconds": 0,
        "result": {"status": "ACCEPTED", "returncode": 0, "timed_out": False},
        "note": record["note"], "accepted_at": record["accepted_at"],
    }


def _test_evidence(test: dict, result: object) -> dict:
    require(isinstance(result, dict) and result.get("status") == "PASS"
            and result.get("returncode") == 0,
            "Acceptance test lacks passing execution evidence")
    return {
        "id": test["id"], "command": test["command"],
        "timeout_seconds": test["timeout_seconds"],
        "result": {
            "status": result["status"], "returncode": result["returncode"],
            "timed_out": bool(result.get("timed_out")),
        },
    }


def _write_markdown(path: Path, report: dict) -> None:
    _write_text(path, _render_markdown(report))


def _render_markdown(report: dict) -> str:
    if report.get("presentation"):
        return render_presentation(report)
    lines = [
        f"# StoryLoop 验收报告：{report['change_id']}", "",
        f"- 状态：{report['status']}",
        f"- 需求摘要：{_markdown(report['requirement']['goal'])}",
        f"- 审批摘要：`{report['requirement']['approval']['contract_digest']}`",
        f"- 实现提交：`{report['implementation_commit']}`", "",
        "## 需求追溯", "",
        "| 用户故事 | 验收条件 | 测试 | 结果 | 证据 SHA-256 |",
        "|---|---|---|---|---|",
    ]
    if report.get("merge_commit"):
        lines.insert(6, f"- 集成提交：`{report['merge_commit']}`")
    for item in report["traceability"]:
        tests = ", ".join(test["id"] for test in item["tests"])
        statuses = ", ".join(test["result"]["status"] for test in item["tests"])
        lines.append(
            f"| {_markdown(item['story']['id'])} {_markdown(item['story']['title'])} | "
            f"{_markdown(item['criterion']['id'])} {_markdown(item['criterion']['then'])} | {tests} | "
            f"{statuses} | `{item['evidence_sha256']}` |"
        )
    lines.extend(["", "## 分层验证", "", "| 层 | 状态 | 命令 / 用例 | 说明 |", "|---|---|---|---|"])
    names = {"unit": "单元测试", "integration": "集成测试", "acceptance": "变更验收", "smoke": "系统冒烟", "checks": "静态检查"}
    for name in LAYERS:
        layer = report["layers"].get(name) or {}
        results = layer.get("results") or []
        passed = sum(1 for r in results if r.get("status") in {"PASS", "PASSED"})
        lines.append(f"| {name}（{names[name]}） | {layer.get('status', '-')} | {passed}/{len(results)} | "
                     f"{_markdown(layer.get('reason', ''))} |")
    lines.extend(_oracle_markdown(report.get("oracle") or {"baseline": {}, "executed": {}, "uncounted": [],
                                                           "skills_used": {}}))
    lines.extend(_correction_markdown(report.get("correction") or {
        "failures": [], "overrules": [], "supersedes": None}))
    lines.extend(["", "## 独立审查", "", _markdown(report["review"]["summary"]), ""])
    lines.extend(routing_markdown(report.get("routing")))
    lines.extend(_telemetry_markdown(report.get("telemetry") or {}))
    return "\n".join(lines)


def _oracle_view(layers: dict, oracle: dict | None, skills_used: dict | None) -> dict:
    """Did each oracle prove it ran: executed counts per command against the old code's baseline."""
    counts = {name: [{"id": item.get("id"), **(item.get("counts") or {})}
                     for item in (layers.get(name) or {}).get("results") or []]
              for name in ("unit", "integration", "acceptance", "smoke")}
    uncounted = sorted({f"{name}:{item}" for name in counts
                        for item in (layers.get(name) or {}).get("uncounted") or []})
    return {"baseline": (oracle or {}).get("baseline") or {}, "executed": counts, "uncounted": uncounted,
            "skills_used": dict(skills_used or {})}


def correction_view(state: dict, change: dict) -> dict:
    from .stop_report import failure_view  # local import: this module is imported by the loop
    return {"failures": [{"attempt": item.get("attempt"),
                           "attribution": item.get("attribution", "implement"),
                           "signature": (item.get("signature") or "")[:8], **failure_view(item)}
                          for item in state.get("failures") or []],
            "overrules": list(state.get("overrules") or []),
            "supersedes": (change.get("supersedes") or {}).get("change_id")}


def _correction_markdown(view: dict) -> list[str]:
    if not view["failures"] and not view["overrules"] and not view["supersedes"]:
        return []
    lines = ["", "## 失败归属", ""]
    if view["supersedes"]:
        lines += [f"修订自 {view['supersedes']}（作废并修订重开）", ""]
    if view["failures"] or view["overrules"]:
        lines += ["| 轮次 | 归属 | 特征 | 说明 |", "|---|---|---|---|"]
        lines += [f"| {item['attempt']} | {item['attribution']} | {item['signature'] or '-'} | "
                  f"{_markdown(item['gate'] + ': ' + item['reason'][:120])} |" for item in view["failures"]]
        lines += [f"| {item.get('attempt')} | 人工驳回 | - | {_markdown(item.get('reason', ''))} |"
                  for item in view["overrules"]]
    return lines


def _oracle_markdown(view: dict) -> list[str]:
    lines = ["", "## 判据自证", "", "| 层 | 命令 / 用例 | 执行数 | 基线 | 解析器 |", "|---|---|---|---|---|"]
    for name, items in view["executed"].items():
        baseline = view["baseline"].get(name) or []
        for index, item in enumerate(items):
            before = baseline[index] if index < len(baseline) else "-"
            executed = "未能统计" if item.get("executed") is None else item["executed"]
            lines.append(f"| {name} | {_markdown(item.get('id'))} | {executed} | {before} | "
                         f"{item.get('parser') or '-'} |")
    if view["uncounted"]:
        lines += ["", "未能统计执行数（这些命令没有自证）：" + ", ".join(view["uncounted"])]
    skills = ", ".join(f"{name} {version}" for name, version in sorted(view["skills_used"].items()))
    lines += ["", f"使用的技能包：{skills or '-'}"]
    return lines


def _telemetry_view(value: dict | None) -> dict:
    base = {"calls": 0, "failed": 0, "agent_ms": 0, "cost_usd": None, "by_role": {}, "attempts": None,
            "loop_ms": 0, "known_cost_usd": None, "input_tokens": None, "output_tokens": None,
            "turns": None, "models": None, "vendors": None, "tiers": None, "skills": None,
            "coverage": {}}
    if isinstance(value, dict):
        base.update({key: value[key] for key in base if key in value})
    return base


def _telemetry_markdown(telemetry: dict) -> list[str]:
    cost = telemetry.get("cost_usd")
    lines = ["## 成本与耗时", "",
             f"- 代理调用：{telemetry.get('calls', 0)} 次（失败 {telemetry.get('failed', 0)} 次），"
             f"代理累计 {_minutes(telemetry.get('agent_ms', 0))}",
             f"- 闭环墙钟：{_minutes(telemetry.get('loop_ms', 0))}，实现尝试 {telemetry.get('attempts') or '-'} 轮",
             f"- 费用：{'未报告' if cost is None else f'{cost:.4f} USD'}", ""]
    by_role = telemetry.get("by_role") or {}
    if by_role:
        lines += ["| 角色 | 调用 | 轮数 | 耗时 | 费用 |", "|---|---|---|---|---|"]
        for role, item in sorted(by_role.items()):
            role_cost = item.get("cost_usd")
            lines.append(f"| {role} | {item.get('calls', 0)} | {item.get('turns', 0)} | "
                         f"{_minutes(item.get('agent_ms', 0))} | "
                         f"{'-' if role_cost is None else f'{role_cost:.4f}'} |")
        lines.append("")
    return lines


def _minutes(milliseconds: object) -> str:
    seconds = int(milliseconds or 0) // 1000
    return f"{seconds // 60} 分 {seconds % 60} 秒"


def _markdown(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _write_text(path: Path, content: str) -> None:
    write_text_atomic(path, content)
