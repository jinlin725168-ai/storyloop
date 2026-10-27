"""Tier-dependent work decomposition, resolved once from engine-owned routing."""
from pathlib import Path

from .capability_protocol import lane_metadata  # noqa: F401  # 兼容再导出：保留旧模块导入路径
from .models import require
from .storage import load_json

PERSPECTIVES = ['correctness', 'security', 'architecture', 'tests', 'oracle-strength']
EXPLORATIONS = ['similar-features', 'call-chain', 'tests-extension']


def subagents_for(role: str, tier: str, payload: dict, capability: dict) -> dict:
    """Describe requested work; lane names never act as implicit path prefixes."""
    result = {'perspectives': [], 'explore': [], 'lanes': False}
    if tier == 'light' or payload.get('mode') == 'retrospective':
        return result
    if role == 'review':
        result['perspectives'] = PERSPECTIVES[:5 if tier == 'heavy' else 4]
    if role == 'design':
        result['explore'] = EXPLORATIONS[:3 if tier == 'heavy' else 2]
    if role == 'implement' and tier == 'heavy':
        plan = payload.get('design_plan') or {}
        touched = plan.get('touched') or []
        prefixes = capability.get('lane_prefixes') or {}
        lanes = {name for name, prefix in prefixes.items() if any(
            isinstance(path, str) and (path == prefix or path.startswith(prefix.rstrip('/') + '/'))
            for path in touched)}
        result['lanes'] = len(lanes) >= 2
    return result


def routing_tier(project: Path, payload: dict) -> str:
    """Prefer persisted engine state; only pre-run calls use engine request context."""
    change = payload.get('change') or {}
    if isinstance(change, dict) and isinstance(change.get('product'), dict):
        if change['product'].get('nature') == 'base':
            return 'heavy'
    change_id = payload.get('change_id') or (change.get('change_id') if isinstance(change, dict) else None)
    if isinstance(change_id, str):
        from .models import identifier
        change_id = identifier(change_id, 'change id')
        path = project / '.storyloop/state/runs' / f'{change_id}.json'
        if path.is_file():
            state = load_json(path)
            tier = (state.get('routing') or {}).get('effective_tier')
            if tier is not None:
                require(isinstance(tier, str) and tier in {'light', 'standard', 'heavy'}, 'Invalid routing tier')
                return tier
    context = payload.get('routing') if isinstance(payload.get('routing'), dict) else {}
    tier = context.get('effective_tier') or payload.get('routing_tier') or payload.get('tier')
    return tier if isinstance(tier, str) and tier in {'light', 'standard', 'heavy'} else 'standard'
