"""Monotonic L3 scope assessment and planning invalidation."""
from copy import deepcopy
from pathlib import Path

from .intake import digest, _signals
from .planning import selected_option
from .project_profile import matches_test_root
from .routing_evidence import actual_scope, measure_scope
from .routing_policy import evaluate
from .routing_state import advance_tier, is_routed
from .smoke_runner import implementation_digest


def scope_expanded(planned: dict, actual: dict) -> bool:
    """Any new path/interface or explicitly unknown field requires a fresh plan."""
    return bool(actual.get('unknown_fields') or any(
        set(actual.get(key, [])) - set(planned.get(key, []))
        for key in ('touched', 'creates', 'interfaces')))


def invalidate_planning(state: dict, reason: str) -> None:
    """Forget decisions, preserving history, spent attempts and evidence-backed locks."""
    planning = state.setdefault('planning', {})
    planning.setdefault('history', []).append({'reason': reason, 'plan': planning.get('plan'),
                                              'attempt': state.get('attempt', 0)})
    for key in ('plan', 'assessment', 'consistency', 'dependency_evidence',
                'continue_heavy', 'pending_option', 'execution_digest', 'characterization_input'):
        planning.pop(key, None)


def observed_scope(project: Path, state: dict, config: dict) -> dict:
    """Exclude engine-owned artifacts while retaining actual existing and created paths."""
    actual = actual_scope(project, state['contract_commit'])
    from .project_boundaries import _category
    from .baseline import load_baseline
    baseline = load_baseline(project)
    roots = config['profile'].get('test_roots', [])
    protected_tests = set((state.get('characterization') or {}).get('tests', []))
    for key in ('touched', 'creates'):
        actual[key] = [p for p in actual[key] if p not in protected_tests and
                       _category(p, baseline, state['change_id'], roots) in {'FEATURE', 'TEST'}]
    planned = state['routing']['scope']
    if not any(set(actual[k]) - set(planned[k]) for k in ('touched', 'creates')):
        actual['unknown_fields'] = []
    actual['interfaces'] = deepcopy(planned['interfaces'])
    actual['symbols'] = [s for s in planned['symbols'] if s['path'] in actual['touched']]
    return actual


def check_l3(project: Path, change: dict, state: dict, config: dict) -> dict | None:
    """Assess safe actual changes and checked positive risks; called only after the boundary gate."""
    if not is_routed(change, state):
        return None
    actual = observed_scope(project, state, config)
    routing = state['routing']
    risks = {r['risk'] for r in routing.get('risk_updates', [])}
    previous = routing.get('assessed_risks', {})
    new_risks = risks - {key for key, value in previous.items() if value == 'yes'}
    if not scope_expanded(routing['scope'], actual) and not new_risks:
        return None
    measured = measure_scope(project, state['contract_commit'],
                             {k: actual[k] for k in ('touched', 'creates', 'interfaces', 'symbols')},
                             routing['policy'])
    facts = _signals(change['product'], measured)
    for key in facts['risks']:
        if previous.get(key) == 'yes' or key in risks:
            facts['risks'][key] = 'yes'
    if actual.get('unknown_fields'):
        facts.update(complete=False, interfaces=None)
    assessed = {'signals': facts, 'scope': actual, 'evidence': measured['evidence'],
                'decision': evaluate(facts, routing['policy'])}
    assessed['id'] = digest(assessed)
    planning = state.get('planning', {})
    if (planning.get('phase') != 'READY' and planning.get('consumed_attempt') == state['attempt']
            and planning.get('l3_assessment', {}).get('id') == assessed['id']):
        return None
    advance_tier(state, assessed['decision'], 'L3', assessed['id'], config)
    routing['assessed_risks'] = deepcopy(facts['risks'])
    invalidate_planning(state, 'L3 scope or risk expansion')
    state['planning'].update(origin='L3', phase='REPLAN', consumed_attempt=state['attempt'],
                             candidate_digest=implementation_digest(project), actual=actual,
                             l3_assessment=assessed)
    state.update(status='DESIGN_REVISION_REQUIRED', phase='planning')
    from .characterization_locks import verified_locks
    from .routing_restore import prepare_restore
    locks = verified_locks(project, state, config)
    roots = config['profile'].get('test_roots', [])
    prepare_restore(project, state, [p for p in actual['touched']
                                    if p not in locks and not matches_test_root(p, roots)])
    return assessed


def enforce_forbidden_paths(state: dict, boundary: dict) -> None:
    """The selected plan can narrow, never broaden, baseline write permissions."""
    plan = state.get('planning', {}).get('plan')
    if not plan:
        return
    from .path_security import forbidden_path_root
    roots = [forbidden_path_root(p) for p in selected_option(plan)['forbidden_paths']]
    forbidden = [item for item in boundary['allowed'] if any(
        item['path'] == root or item['path'].startswith(root + '/') for root in roots)]
    if forbidden:
        boundary['allowed'] = [item for item in boundary['allowed'] if item not in forbidden]
        boundary['violations'].extend({'path': item['path'], 'category': 'PLAN_FORBIDDEN'}
                                      for item in forbidden)
        boundary['status'] = 'BOUNDARY_VIOLATION'
