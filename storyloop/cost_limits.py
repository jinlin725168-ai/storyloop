"""Per-change runaway protection, using observed usage rather than estimated prices."""
from __future__ import annotations

import math
from pathlib import Path

from .models import StoryLoopError, require
from .telemetry import ledger_entries

DEFAULTS = {'max_calls': 80, 'max_agent_wall_seconds': 28800,
            'max_tokens': None, 'max_cost_usd': None}


def configured_limits(config: dict) -> dict:
    """Validate explicit limits and apply the same defaults to legacy projects."""
    supplied = config.get('limits', {})
    require(isinstance(supplied, dict) and set(supplied) <= set(DEFAULTS), 'Invalid limits')
    limits = {**DEFAULTS, **supplied}
    for name, value in limits.items():
        require(value is None or (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value >= 0), f'Invalid limits.{name}')
    return limits


def _totals(entries: list[dict]) -> dict:
    return {'calls': len(entries),
            'agent_wall_seconds': sum(row.get('wall_ms', 0) for row in entries) / 1000,
            'tokens': sum(row.get('input_tokens', 0) + row.get('output_tokens', 0) for row in entries),
            'cost_usd': sum(row.get('cost_usd', 0) for row in entries)}


def check_before_call(project: Path, change_id: str | None, config: dict) -> None:
    """Stop before starting another call once any configured dimension is reached."""
    limits = configured_limits(config)
    if change_id in {None, 'pending', 'foundation'}:
        return
    entries = ledger_entries(project, change_id)
    used = _totals(entries)
    if not any(value is not None and used[key.removeprefix('max_')] >= value
               for key, value in limits.items()):
        return
    roles = sorted({row['role'] for row in entries})
    details = {'used': used, 'limits': limits,
               'by_role': {role: _totals([row for row in entries if row['role'] == role]) for role in roles},
               'cost_coverage': {'known': sum('cost_usd' in row for row in entries), 'total': len(entries)}}
    raise StoryLoopError('Change agent cost limit reached', code='COST_LIMIT_REACHED', details=details)


def stop_fields(project: Path | str, change_id: str, details: dict) -> dict:
    """Add optional evidence without changing legacy stop-report shapes."""
    result = {key: details[key] for key in ('used', 'limits', 'by_role', 'cost_coverage') if key in details}
    entries = ledger_entries(Path(project), change_id)
    if any(row.get('degraded') == 'cross_vendor' for row in entries):
        result['degraded'] = 'cross_vendor'
    return result


def render_usage(stop: dict) -> list[str]:
    """Render observed totals and explicitly disclose partial cost coverage."""
    lines = []
    if stop.get('degraded') == 'cross_vendor':
        lines.append('🔧 本次审查与实现同一厂商')
    if 'used' in stop:
        lines.append('成本上限：')
        for key, used in stop['used'].items():
            limit = stop['limits'].get('max_' + key)
            lines.append(f'  - {key}: {used} / {limit if limit is not None else "未设置"}')
        for role, usage in stop.get('by_role', {}).items():
            lines.append(f'  - {role}: {usage}')
    coverage = stop.get('cost_coverage')
    if coverage and coverage['known'] < coverage['total']:
        lines.append(f"费用上限只覆盖 {coverage['known']}/{coverage['total']} 次调用")
    return lines
