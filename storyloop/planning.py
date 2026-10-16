"""L2 tiered planning under the run lock, before characterization and implementation."""
from copy import deepcopy
from pathlib import Path

from .answers import load_answers
from .facts import merge_facts
from .intake import digest, _signals, _role_evidence
from .principles import check_principles, load_principles
from .planning_validation import option_scope, validate_plan
from .project_loop_guards import read_only_call
from .project_loop_support import pause, save_run
from .routing_evidence import measure_scope
from .routing_policy import evaluate
from .routing_state import advance_tier
from .semantic_repair import answer_with_repair


def required_locks(tier: str, nature: str, touched: list[str]) -> list[str]:
    """Heavy and refactor work lock all selected existing paths before implementation."""
    return sorted(set(touched)) if tier == 'heavy' or nature == 'refactor' else []


def selected_option(plan: dict) -> dict:
    """Project the explicitly approved option, or the main option before approval."""
    selected = (plan.get('approval') or {}).get('option_id', plan['main_option'])
    return next(option for option in plan['options'] if option['id'] == selected)


def ensure_routing_plan(project: Path, change: dict, state: dict, services,
                        config: dict) -> dict | None:
    """Build and measure a complete plan; approval/consistency gates run after this gate."""
    planning = state.setdefault('planning', {})
    if (planning.get('plan') or {}).get('status') == 'DONE':
        return None
    state['phase'] = 'planning'
    response = None
    if (state['routing']['effective_tier'] == 'light' and planning.get('origin') != 'L3'
            and not load_principles(project)['principles']):
        response = _compact_plan(change)
        plan = validate_plan(response, project, change, state)
        assessed = assess_plan(project, change, state, plan)
        _record_assessment(state, config, assessed)
        if state['routing']['effective_tier'] != 'light' or assessed['decision']['unresolved']:
            response = None
    if response is None:
        if services.design is None:
            return pause(project, state, 'NEEDS_INPUT', phase='planning',
                         questions=['Provide a design service to complete the routing plan.'])
        response = _design(project, change, state, services)
        plan = validate_plan(response, project, change, state)
    if plan['status'] == 'NEEDS_INPUT':
        return pause(project, state, 'NEEDS_INPUT', phase='planning', questions=plan['questions'])
    assessed = assess_plan(project, change, state, plan)
    _record_assessment(state, config, assessed)
    if assessed['decision']['unresolved']:
        return pause(project, state, 'NEEDS_INPUT', phase='planning', questions=[
            'Complete plan evidence: ' + ', '.join(assessed['decision']['unresolved'])])
    planning['plan'] = plan
    merge_facts(project, state, response.get('facts'), 'design')
    state.pop('questions', None)
    state.pop('phase', None)
    save_run(project, state)
    return None


def _design(project: Path, change: dict, state: dict, services) -> dict:
    context = {'mode': 'routing-plan', 'change': change,
               'routing': deepcopy(state['routing']),
               'answers': load_answers(project, change['change_id']),
               'facts': list(state.get('facts') or []), 'principles': load_principles(project),
               'replanning': deepcopy(state.get('planning', {}))}
    return answer_with_repair(
        lambda request: read_only_call(project, change['change_id'],
                                       lambda: services.design(project, request)),
        context, lambda answer: validate_plan(answer, project, change, state))


def _compact_plan(change: dict) -> dict:
    product = change['product']
    assessment = product['assessment']
    scope = deepcopy(assessment['scope'])
    paths = scope['touched'] + scope['creates']
    constraints = [{'criterion_id': c['id'], 'path': path, 'rule': c['then']}
                   for story in product['stories'] for c in story['acceptance'] for path in paths]
    option = {'id': 'main', 'summary': product['goal'], **scope,
              'risks': deepcopy(assessment['risks']), 'forbidden_paths': [],
              'constraints': constraints, 'regression': ['Run all configured verification layers'],
              'migration': {'steps': [], 'reason': 'Approved assessment reports no migration'},
              'dependencies': [], 'principle_checks': []}
    return {'main_option': 'main', 'options': [option], 'questions': [],
            'design': product['goal']}


def assess_plan(project: Path, change: dict, state: dict, plan: dict) -> dict:
    """Recompute selected-option signals; previously established yes risks cannot disappear."""
    option = selected_option(plan)
    measured = measure_scope(project, state['contract_commit'], option_scope(option),
                             state['routing']['policy'])
    product = deepcopy(change['product'])
    previous = state['routing'].get('assessed_risks', {})
    for key, item in option['risks'].items():
        old = product['assessment']['risks'][key]['value']
        value = 'yes' if 'yes' in (old, previous.get(key), item['value']) else item['value']
        product['assessment']['risks'][key] = {**item, 'value': value}
    principles = load_principles(project)
    deviations = check_principles(option, principles)
    signals = _signals(product, measured)
    if deviations:
        signals['risks']['principle_deviation'] = 'yes'
    elif any(item['verdict'] == 'unknown' for item in option['principle_checks']):
        if signals['risks']['principle_deviation'] != 'yes':
            signals['risks']['principle_deviation'] = 'unknown'
    decision = evaluate(signals, state['routing']['policy'])
    data = {'signals': signals, 'evidence': measured['evidence'] + _role_evidence(
                {'risks': option['risks'], 'criteria': []}, state['contract_commit']),
            'scope': measured['scope'], 'decision': decision,
            'principles': {**principles, 'deviations': deviations}}
    return {**data, 'id': digest(data)}


def _record_assessment(state: dict, config: dict, assessed: dict) -> None:
    advance_tier(state, assessed['decision'], 'L2', assessed['id'], config)
    state['routing']['scope'] = deepcopy(assessed['scope'])
    state['routing']['assessed_risks'] = deepcopy(assessed['signals']['risks'])
    state.setdefault('planning', {})['assessment'] = assessed


def plan_projection(state: dict, *, locks_only: bool = False, nature: str = 'feature') -> dict | None:
    """Role payload preserving the complete selected option and the bound plan identifier."""
    plan = state.get('planning', {}).get('plan')
    if not plan or plan['status'] != 'DONE':
        return None
    option = deepcopy(selected_option(plan))
    if locks_only:
        option['touched'] = required_locks(state['routing']['effective_tier'], nature, option['touched'])
    return {**option, 'plan_id': plan['id'], 'design': plan['design']}


def review_focus(state: dict) -> dict:
    """Vary presentation only; all review contract fields and deterministic checks remain."""
    tier = state.get('routing', {}).get('effective_tier')
    if tier is None:
        return {}
    perspectives = ['engineering', 'product', 'security', 'quality']
    if tier == 'light':
        perspectives = ['contract, regression, boundaries, security and oracle correctness']
    elif tier == 'heavy':
        perspectives.append('oracle')
    return {'review_focus': perspectives, 'routing_tier': tier}
