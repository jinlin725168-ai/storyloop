"""Tier-dependent work decomposition, resolved once from engine-owned routing."""
from pathlib import Path

from .models import require
from .path_security import repository_relative
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


def lane_metadata(value: dict) -> dict | None:
    """Validate optional capability metadata without accepting executable/path syntax."""
    names, prefixes = value.get('lanes', []), value.get('lane_prefixes', {})
    if (not isinstance(names, list) or len(names) > 32 or
            not all(isinstance(name, str) and name and len(name) <= 80 for name in names)
            or not isinstance(prefixes, dict) or set(prefixes) - set(names)):
        return None
    from .models import StoryLoopError
    try:
        for prefix in prefixes.values():
            require(isinstance(prefix, str) and not any(c in prefix for c in '*?[];|&$()`\n\r'), 'Unsafe lane prefix')
            require(repository_relative(prefix) == prefix.rstrip('/'), 'Invalid lane prefix')
    except StoryLoopError:
        return None
    return {key: value[key] for key in ('lanes', 'lane_prefixes') if key in value}
