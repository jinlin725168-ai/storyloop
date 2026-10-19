"""Model selection and truthful single-session profile reporting for CLI adapters."""
from __future__ import annotations

from .models import StoryLoopError


def model_for(request: dict, models: dict | None) -> str | None:
    """Resolve a requested tier, rejecting configured but missing mappings."""
    profile = request.get('role_profile')
    if not profile or models is None:
        return None
    model = models.get(profile.get('tier'))
    if not isinstance(model, str) or not model:
        raise StoryLoopError('ADAPTER_PROFILE_UNSUPPORTED', code='ADAPTER_PROFILE_UNSUPPORTED')
    return model


def sequential_profile(request: dict, vendor: str, model: str | None) -> dict:
    """Describe the selected model and the single-session fallback, without inventing cost."""
    profile = request.get('role_profile')
    if not profile:
        return {}
    result = {'vendor': vendor, 'path_guard': 'unsupported'}
    if model:
        result.update(model=model, tier_applied=profile['tier'])
    requested = profile.get('subagents') or {}
    result['subagents_applied'] = {'mode': 'sequential',
        'perspectives': len(requested.get('perspectives') or []),
        'explore': len(requested.get('explore') or []), 'lanes': 0}
    return result


def sequential_instructions(request: dict) -> str:
    """Ask one session to cover the requested perspectives and exploration in order."""
    profile = request.get('role_profile')
    if not profile:
        return ''
    requested = profile.get('subagents') or {}
    return ('\nSubagents are unavailable: use sequential analysis in this session. '
            f"Review perspectives: {', '.join(requested.get('perspectives') or [])}. "
            f"Explore: {', '.join(requested.get('explore') or [])}. Do not spawn lanes.\n")


def tier_models(values: list[str]) -> dict | None:
    """Parse repeatable CLI tier=model values; absence preserves the legacy model choice."""
    if not values:
        return None
    result = {}
    for value in values:
        tier, separator, model = value.partition('=')
        if not separator or tier not in {'strong', 'standard'} or not model.strip() or tier in result:
            raise ValueError('tier-model must uniquely map strong/standard to a nonempty model')
        result[tier] = model
    return result


def cli_capabilities(models: dict | None, vendor: str) -> dict:
    """Advertise only implemented profile capabilities and explicitly mapped tiers."""
    return {'vendor': vendor, 'tiers': list(models or {}), 'subagents': False,
            'path_guard': False, 'fast_checks': False, 'modes': ['*']}


def diagnostic_fields(telemetry: dict) -> dict:
    """Keep only bounded, well-formed adapter claims for diagnostics."""
    result = {}
    guard = telemetry.get('path_guard')
    if isinstance(guard, str) and guard in {'enforced', 'unsupported'}:
        result['path_guard'] = guard
    agents = telemetry.get('subagents_applied')
    if not isinstance(agents, dict) or set(agents) != {'mode', 'perspectives', 'explore', 'lanes'}:
        return result
    mode = agents['mode']
    counts = [agents[key] for key in ('perspectives', 'explore', 'lanes')]
    if (isinstance(mode, str) and mode in {'parallel', 'sequential'}
            and all(type(value) is int and 0 <= value <= 10_000 for value in counts)):
        result['subagents_applied'] = dict(agents)
    return result
