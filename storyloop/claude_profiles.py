"""Profile-driven Claude orchestration helpers independent of the CLI entrypoint."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

from .adapter_profiles import tier_models
from .claude_permissions import profile_command
from .models import require
from .path_security import forbidden_path_root


def capabilities(models: dict | None, lanes: dict) -> dict:
    """Advertise the implemented profile features and explicit configured lane prefixes."""
    return {'vendor': 'anthropic', 'tiers': list(models or {}), 'subagents': True,
            'path_guard': True, 'fast_checks': True, 'modes': ['*'],
            'lanes': list(lanes), 'lane_prefixes': lanes}


def profile_telemetry(request: dict, model: str | None, *, perspectives=0, explore=0, lanes=0) -> dict:
    """Report work actually dispatched; an enabled-but-unused lane plan is zero lanes."""
    if 'role_profile' not in request:
        return {}
    result = {'vendor': 'anthropic', 'path_guard': 'enforced', 'subagents_applied': {
        'mode': 'parallel' if perspectives or explore or lanes else 'sequential',
        'perspectives': perspectives, 'explore': explore, 'lanes': lanes}}
    if model:
        result['tier_applied'] = request['role_profile']['tier']
    return result


def _rebuild(base: list[str], request: dict) -> list[str]:
    model = base[base.index('--model') + 1] if '--model' in base else None
    turns = int(base[base.index('--max-turns') + 1])
    # Preserve already validated explicit Bash rules, while narrowing filesystem permissions.
    start, end = base.index('--allowedTools') + 1, base.index('--disallowedTools')
    bash = [rule[5:-1] for rule in base[start:end] if rule.startswith('Bash(')]
    return profile_command(base[0], request, turns, bash, model)


def readonly_command(base: list[str], request: dict) -> list[str]:
    if 'role_profile' not in request:
        return base
    readonly = deepcopy(request)
    readonly['role'] = 'design'
    readonly['role_profile']['writable_paths'] = []
    return _rebuild(base, readonly)


def lane_command(base: list[str], request: dict, prefix: str) -> list[str]:
    """Intersect the lane prefix with the parent's allowlist; never expand write access."""
    if 'role_profile' not in request:
        return base
    narrowed = deepcopy(request)
    writable = []
    for pattern in request['role_profile'].get('writable_paths', []):
        root = forbidden_path_root(pattern)
        if root == prefix or root.startswith(prefix.rstrip('/') + '/'):
            writable.append(pattern)
        elif pattern.endswith('/**') and prefix.startswith(root + '/'):
            writable.append(prefix.rstrip('/') + '/**')
    narrowed['role_profile']['writable_paths'] = writable
    return _rebuild(base, narrowed)


def explore_design(request, base, args, run, prompt, new_telemetry, merge):
    """Run read-only standard-tier explorations and pass their findings to the design lead."""
    names = request['role_profile']['subagents']['explore']
    require(all(name in {'similar-features', 'call-chain', 'tests-extension'} for name in names),
            'Unknown exploration focus')
    models = tier_models(args.tier_model)
    explore_base = readonly_command(base, request)
    if models is not None:
        require(bool(models.get('standard')), 'Exploration requires standard model mapping')
        if '--model' in explore_base:
            explore_base[explore_base.index('--model') + 1] = models['standard']
        else:
            explore_base += ['--model', models['standard']]

    def explore(name):
        child = deepcopy(request)
        child['role_profile']['tier'] = 'standard'
        child['role_profile']['subagents'] = {'perspectives': [], 'explore': [], 'lanes': False}
        text = prompt(child, 'design', args.guide, extra=(f'[design exploration: {name}]\n'
            'Read only. Report relevant paths, evidence and extension opportunities as {summary:string}.'), schema=False)
        result, telemetry, _ = run(explore_base, text, 'design/' + name, args, request.get('project'))
        return name, result, telemetry

    with ThreadPoolExecutor(max_workers=min(3, len(names))) as pool:
        reports = list(pool.map(explore, names))
    total = new_telemetry()
    for _, _, telemetry in reports:
        merge(total, telemetry)
    if any(result is None for _, result, _ in reports):
        return None, total
    lead = deepcopy(request)
    lead['payload'] = {**lead.get('payload', {}), 'exploration': {name: result for name, result, _ in reports}}
    return lead, total
