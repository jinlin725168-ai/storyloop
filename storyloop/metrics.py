"""Read-only orchestration and explicit persistence for cross-change metrics."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from .ci_results import load_ci_results
from .metrics_collect import collect
from .metrics_compute import (
    base_human_review_substitutions,
    attempts,
    contract_ratio,
    escapes,
    first_pass,
    forced_heavy,
    interventions,
    lessons,
    local_green_ci_red,
    overrule_rate,
    rework,
    stuck,
    upgrades,
)
from .metrics_cost import cost
from .metrics_rules import MANUAL_DEFERRED, deferred_signals, suggest, thresholds
from .models import StoryLoopError, require
from .path_security import safe_absolute_target
from .report_evidence import _read_object
from .storage import write_json, write_text_atomic


MAX_CONFIG_BYTES = 2_000_000


def build_metrics(project: Path, since: date | None, ci_results: Path | None) -> dict:
    """Build one evidence-bound metrics snapshot without changing the project."""
    root = Path(project).resolve()
    require(root.is_dir(), "metrics 项目必须是已存在的目录")
    collected = collect(root, since)
    records = collected["changes"]
    ci_values, ci_missing = _ci_values(root, ci_results, records)
    metrics = _compute_metrics(root, records, ci_values)
    config = _metrics_config(root)
    return {
        "status": "METRICS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "since": since.isoformat() if since is not None else None,
        "changes": len(records),
        "metrics": metrics,
        "suggestions": suggest(metrics, thresholds(config)),
        "deferred": deferred_signals(records),
        "manual_deferred": list(MANUAL_DEFERRED),
        "missing": _sorted_unique(collected["missing"] + ci_missing),
        "discrepancies": collected["discrepancies"],
    }


def _ci_values(
    root: Path, folder: Path | None, records: list[dict],
) -> tuple[dict[str, dict] | None, list[dict]]:
    if folder is None:
        return None, []
    values, missing = load_ci_results(root, Path(folder), records)
    return values, missing


def _compute_metrics(
    root: Path, records: list[dict], ci_values: dict[str, dict] | None,
) -> dict:
    values = (
        first_pass(records), attempts(records), upgrades(records), rework(records),
        stuck(records), escapes(records), local_green_ci_red(ci_values, records),
        forced_heavy(records), overrule_rate(records), contract_ratio(root, records),
        interventions(records), cost(records), lessons(root, records),
        base_human_review_substitutions(records),
    )
    return {value["id"]: value for value in values}


def _metrics_config(root: Path) -> dict:
    path = root / ".storyloop/config.json"
    if not path.exists() and not path.is_symlink():
        return {}
    value = _read_object(root, path, MAX_CONFIG_BYTES)
    if value is None:
        raise StoryLoopError("无法安全读取 metrics 配置：config.json")
    return value


def save_metrics(project: Path, result: dict) -> list[str]:
    """Atomically overwrite the two dated report files inside the project."""
    root = Path(project).resolve()
    require(root.is_dir(), "metrics 项目必须是已存在的目录")
    stamp = _utc_date(result.get("generated_at"))
    folder = root / "reports/storyloop/metrics"
    markdown = folder / f"{stamp}.md"
    json_path = folder / f"{stamp}.json"
    for target in (markdown, json_path):
        safe_absolute_target(target, "metrics report path")
    folder.mkdir(parents=True, exist_ok=True)
    relative = [markdown.relative_to(root).as_posix(), json_path.relative_to(root).as_posix()]
    from .metrics_render import render_metrics
    write_text_atomic(markdown, render_metrics(result) + "\n")
    write_json(json_path, {**result, "written": relative})
    return relative


def _utc_date(value: object) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise StoryLoopError("generated_at 必须是带时区的 ISO 时间") from exc
    if parsed.tzinfo is None:
        raise StoryLoopError("generated_at 必须是带时区的 ISO 时间")
    return parsed.astimezone(timezone.utc).date().isoformat()


def _sorted_unique(items: list[dict]) -> list[dict]:
    unique = {(item.get("change_id"), item.get("reason")): item for item in items}
    return [unique[key] for key in sorted(unique, key=lambda item: (str(item[0]), str(item[1])))]
