"""Requirement-linked product acceptance reports."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from .baseline import _git
from .changes import load_change
from .models import require
from .smoke import load_smoke_manifest, smoke_digests
from .smoke_runner import git_visible_digest
from .storage import load_json, write_bytes_atomic, write_json, write_text_atomic

COMMIT = re.compile(r"[0-9a-fA-F]{40,64}")


def generate_change_report(
    project: Path,
    change_id: str,
    implementation_commit: str,
    regression: dict,
    smoke: dict,
    review: dict,
    manual_acceptance: list[dict] | None = None,
    telemetry: dict | None = None,
) -> Path:
    """Write a passing report only when every link has real green or human evidence."""
    project = Path(project).resolve()
    change = load_change(project, change_id)
    evidence_digest = _validate_gate(
        project, change, implementation_commit, regression, smoke, review
    )
    manifest = load_smoke_manifest(project, change_id)
    traceability = _build_traceability(
        change, manifest, smoke.get("results", []), evidence_digest, manual_acceptance or []
    )
    report = {
        "version": 1,
        "status": "PASSED",
        "change_id": change_id,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "requirement": {
            "goal": change["product"]["goal"],
            "approval": change["approval"],
            "revision": change["revision"],
        },
        "implementation_commit": implementation_commit,
        "regression": regression,
        "review": review,
        "traceability": traceability,
        "telemetry": _telemetry_view(telemetry),
    }
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
    project: Path, change: dict, implementation_commit: str, regression: dict,
    smoke: dict, review: dict,
) -> str:
    require(change["approval"], "Acceptance report requires an approved contract")
    require(isinstance(implementation_commit, str)
            and COMMIT.fullmatch(implementation_commit) is not None,
            "Acceptance report requires a valid implementation commit")
    require(_git(project, "rev-parse", "HEAD").strip() == implementation_commit,
            "Implementation commit is not the tested current candidate")
    require(regression.get("status") in {"PASSED", "SKIPPED"}, "Regression did not pass")
    require(smoke.get("status") == "PASSED", "Smoke acceptance did not pass")
    require(review.get("verdict") == "pass"
            and not review.get("findings") and not review.get("questions"),
            "Independent review did not pass")
    evidence_path = project / ".storyloop/evidence" / change["change_id"] / "smoke-result.json"
    evidence = load_json(evidence_path)
    evidence_digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    supplied = {key: value for key, value in smoke.items()
                if key not in {"evidence_path", "evidence_sha256"}}
    require(smoke.get("evidence_sha256") == evidence_digest and supplied == evidence,
            "Smoke evidence does not match the protected evidence file")
    require(evidence.get("suite_digests") == smoke_digests(project, change["change_id"]),
            "Smoke evidence is not bound to the protected test bytes")
    require(evidence.get("workspace_sha256") == git_visible_digest(project),
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
    lines.extend(["", "## 独立审查", "", _markdown(report["review"]["summary"]), ""])
    lines.extend(_telemetry_markdown(report.get("telemetry") or {}))
    return "\n".join(lines)


def _telemetry_view(value: dict | None) -> dict:
    base = {"calls": 0, "failed": 0, "agent_ms": 0, "cost_usd": None, "by_role": {}, "attempts": None,
            "loop_ms": 0}
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
