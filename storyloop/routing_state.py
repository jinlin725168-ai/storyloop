"""Pure routing snapshots and version-aware execution budgets."""
from copy import deepcopy
from .intake import digest
from .models import require
from .routing_policy import DEFAULT_BUDGETS, validate_policy


def is_routed(change: dict, state: dict | None = None) -> bool:
    """Existing runs own their protocol; adding draft metadata never upgrades them."""
    if state is not None:
        return isinstance(state.get('routing'), dict) and state['routing'].get('version') == 1
    return change.get('routing_version') == 1


def resolve_budget(config: dict, tier: str, *, legacy: bool = False) -> dict:
    """Resolve an explicit positive budget before protocol defaults."""
    if 'max_attempts' in config:
        value = config['max_attempts']
        require(type(value) is int and value > 0, 'Invalid max_attempts')
        return {'limit': value, 'source': 'explicit'}
    if legacy:
        return {'limit': 3, 'source': 'legacy'}
    require(tier in DEFAULT_BUDGETS, 'Invalid routing tier')
    return {'limit': DEFAULT_BUDGETS[tier], 'source': 'tier'}


def open_routing(change: dict, policy: dict, config: dict) -> dict:
    """Freeze approved L1, policy and scope without retaining caller-owned references."""
    frozen = validate_policy(policy)
    tier = change['approval']['tier']
    budget = resolve_budget(config, tier)
    return {'version': 1, 'initial_tier': tier, 'effective_tier': tier,
            'policy': frozen, 'policy_digest': digest(frozen),
            'reasons': deepcopy(change['intake']['decision']['reasons']), 'history': [],
            'scope': deepcopy(change['product']['assessment']['scope']),
            'budget_source': budget['source'], 'limit': budget['limit']}


def run_config(config: dict, change: dict, state: dict) -> dict:
    """Resolve runtime limits once so implement payload and loop share the same budget."""
    routed = is_routed(change, state)
    routing = state.get('routing', {})
    budget = resolve_budget(config, routing.get('effective_tier'), legacy=not routed)
    if routed:
        # Default tier escalation never shrinks an already granted default budget.
        if budget['source'] == 'tier' and routing['budget_source'] == 'tier':
            budget['limit'] = max(budget['limit'], routing['limit'])
        routing.update(limit=budget['limit'], budget_source=budget['source'])
    resolved = {**config, 'max_attempts': budget['limit']}
    if routed:
        resolved['routing'] = deepcopy(routing['policy'])
    return resolved
