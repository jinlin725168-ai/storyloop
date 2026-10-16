"""Who has to fix a failed attempt, and whether the loop is still making progress.

The engine decides what it can observe (a layer red only with environment errors is `environment`);
the review proposes a target for each finding and the engine checks the evidence before believing it
(subproject 2a, decisions Q1 and Q5). Signatures use only stable identifiers, never message text.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from typing import Callable

TARGETS = ("implementation", "oracle", "contract")
LAYER_NAMES = ("unit", "integration", "acceptance", "smoke", "checks")
LOCATION = re.compile(r"^(?P<path>[^:]+?)(?::\d+)*$")
EVIDENCE_LOCATION = re.compile(r"[^:]+:[1-9][0-9]*(?::[1-9][0-9]*)?")


def contract_ids(change: dict) -> set[str]:
    """Return the story and acceptance identifiers in a validated contract."""
    stories = change["product"]["stories"]
    return {story["id"] for story in stories} | {c["id"] for story in stories for c in story["acceptance"]}


def location_file(location: object) -> str | None:
    """The repository-relative file of a `path:line` location, or None when it is not one."""
    if not isinstance(location, str) or any(ord(char) < 32 or ord(char) == 127 for char in location):
        return None
    match = LOCATION.match(location.strip().replace("\\", "/"))
    if not match:
        return None
    path = match.group("path").strip()
    if not path or path.startswith("/") or ".." in path.split("/"):
        return None
    normalized = PurePosixPath(path).as_posix()
    return normalized if normalized != "." else None


def finding_key(item: dict) -> str:
    """Stable finding identity, independent of message wording and line movement."""
    return f"{item.get('criterion_id') or '-'}|{location_file(item.get('location')) or '-'}"


def check_findings(findings: list, change: dict, oracle_paths: list[str],
                   exists: Callable[[str], bool], overruled: set[str]) -> list[dict]:
    """Copies of the findings, each with a target the evidence supports."""
    ids = contract_ids(change)
    checked = []
    for finding in findings:
        item = dict(finding) if isinstance(finding, dict) else {"message": str(finding)}
        target = item.get("target") or "implementation"
        if target != "implementation" and finding_key(item) in overruled:
            item["attribution_note"] = "已被人驳回，按实现问题处理"
            target = "implementation"
        problem = _evidence_problem(item, target, ids, oracle_paths, exists)
        if problem:
            item["attribution_note"] = f"归属证据不足（{problem}），按实现问题处理"
            target = "implementation"
        item["target"] = target
        checked.append(item)
    return checked


def _evidence_problem(item: dict, target: str, ids: set[str], oracle_paths: list[str],
                      exists: Callable[[str], bool]) -> str | None:
    if target == "implementation":
        return None
    if target not in TARGETS:
        return f"未知的 target {target}"
    if item.get("criterion_id") not in ids:
        return "criterion_id 不在合同里"
    path = location_file(item.get("location"))
    if path is None or not EVIDENCE_LOCATION.fullmatch(item["location"].strip()):
        return "location 必须包含正整数行号，可附正整数列号"
    if not exists(path):
        return "location 指向的文件不存在"
    roots = [location_file(root) for root in oracle_paths]
    if target == "oracle" and not any(root and (path == root or path.startswith(root + "/"))
                                      for root in roots):
        return "location 不在验收套件或特征化测试里"
    return None


def route(findings: list[dict]) -> str:
    """A wrong contract invalidates oracle and code alike; a wrong oracle cannot be satisfied by code."""
    targets = {item.get("target") for item in findings}
    for target in ("contract", "oracle"):
        if target in targets:
            return target
    return "implementation"


def layer_attribution(layers: dict) -> str:
    """Attribute exclusively environmental failures without hiding assertion failures."""
    red = [layers[name] for name in LAYER_NAMES
           if isinstance(layers.get(name), dict) and layers[name].get("status") not in {"PASSED", "SKIPPED"}]
    return "environment" if red and all(_environment_layer(layer) for layer in red) else "implement"


def _environment_layer(layer: dict) -> bool:
    if layer.get("oracle_problems"):
        return False
    bad = [result for result in layer.get("results") or []
           if result.get("status") not in {"PASS", "NOT_RUN"}]
    environment = {"ERROR", "TIMEOUT"}
    # Every red layer needs its own environmental evidence, not another layer's.
    return (all(result.get("status") in environment for result in bad)
            and (bool(bad) or layer.get("status") in environment))


def failure_signature(failure: dict) -> str:
    """Hash stable failure identifiers; ignore text, ordering, and duplicate evidence."""
    parts: set[str] = set()
    for name in LAYER_NAMES:
        layer = failure.get(name)
        if isinstance(layer, dict) and layer.get("status") not in {"PASSED", "SKIPPED"}:
            parts.add(f"layer:{name}:{layer.get('status')}")
            parts.update(f"case:{name}:{r.get('id')}:{r.get('status')}"
                         for r in layer.get("results") or [] if r.get("status") != "PASS")
    if "characterization" in failure:
        parts.update(f"lock:{path}" for path in failure["characterization"].get("uncharacterized") or [])
    if "review" in failure:
        findings = [f for f in (failure["review"] or {}).get("findings") or [] if isinstance(f, dict)]
        parts.update(f"review:{f.get('target') or 'implementation'}:{_review_identity(f)}"
                     for f in findings)
        if not findings:
            parts.add("review:no-findings")
    if "contract" in failure:
        parts.add(f"contract:{failure['contract'].get('code') or 'INVALID'}")
    for item in (failure.get("boundary") or {}).get("violations") or []:
        parts.add(f"boundary:{item.get('path')}:{item.get('category')}")
    return hashlib.sha256("\n".join(sorted(parts)).encode("utf-8")).hexdigest()


def _review_identity(finding: dict) -> str:
    if not finding.get("criterion_id") and not location_file(finding.get("location")):
        return f"{finding_key(finding)}:{finding.get('perspective') or '-'}"
    return finding_key(finding)


def _counted(failures: list[dict], epoch: int) -> list[dict]:
    # Unknown legacy signatures interrupt a streak: equality cannot be established.
    return [item for item in failures if int(item.get("epoch", 0)) == epoch]


def _consecutive(failures: list[dict]) -> bool:
    """失败必须在真实轮次中相邻；问答即使保留红层证据，也中断连续性。"""
    if any((item.get("review") or {}).get("verdict") == "needs_input" for item in failures):
        return False
    attempts = [item.get("attempt") for item in failures]
    return (all(type(attempt) is int and attempt > 0 for attempt in attempts)
            and all(right == left + 1 for left, right in zip(attempts, attempts[1:])))


def next_mode(failures: list[dict], epoch: int) -> str | None:
    """Two attempts in a row with the same signature: the next one investigates the root cause first."""
    recent = _counted(failures, epoch)[-2:]
    if (len(recent) == 2 and _consecutive(recent) and recent[0].get("signature")
            and recent[0]["signature"] == recent[1].get("signature")):
        return "root_cause"
    return None


def no_progress(failures: list[dict], epoch: int) -> bool:
    """The root-cause attempt changed nothing: same signature (so no failing case turned green)."""
    recent = _counted(failures, epoch)[-3:]
    return (len(recent) == 3 and _consecutive(recent) and recent[-1].get("mode") == "root_cause"
            and bool(recent[-1].get("signature"))
            and recent[0].get("signature") == recent[1].get("signature") == recent[2]["signature"])
