"""Read-only heavy consistency review after approval, before characterization."""
from copy import deepcopy
from pathlib import Path

from .answers import load_answers
from .baseline import _git
from .consistency_locations import validate_plan_location
from .design_approval import approval_matches, current_binding, _valid_design
from .evidence_locations import evidence_location_exists
from .failure_attribution import finding_key, location_file
from .intake import digest
from .models import StoryLoopError, require, text
from .path_security import safe_target
from .planning import selected_option
from .principles import load_principles
from .project_git import changed_paths, commit_paths
from .guarded_calls import read_only_call
from .project_loop_support import save_run
from .run_transitions import transition
from .run_stops import pause
from .role_schemas import schema_errors, schema_for
from .routing_state import is_routed
from .semantic_repair import answer_with_repair
from .smoke import suite_relative
from .storage import write_json


def consistency_status(findings: list[dict]) -> str:
    """Route all blocking findings by contract, oracle, then design priority."""
    targets = {item['target'] for item in findings if item['blocking']}
    for target, status in (('contract', 'NEEDS_SPEC_FIX'), ('oracle', 'ORACLE_DEFECT'),
                           ('design', 'DESIGN_REVISION_REQUIRED')):
        if target in targets:
            return status
    return 'COHERENT'


def validate_consistency(answer: object, project: Path, change: dict, state: dict) -> dict:
    """Reject contradictory verdicts and unsupported references; never downgrade bad evidence."""
    errors = schema_errors(answer, schema_for('review', {'mode': 'consistency'}))
    require(not errors, 'Invalid consistency response: ' + '; '.join(errors))
    text(answer['summary'], 'consistency summary')
    for question in answer['questions']:
        text(question, 'consistency question')
    criteria = {c['id'] for story in change['product']['stories'] for c in story['acceptance']}
    for item in answer['findings']:
        require(item['criterion_id'] in criteria, 'Consistency references unknown criterion')
        text(item['message'], 'consistency finding')
        _validate_location(item, project, change, state)
    blocked = any(item['blocking'] for item in answer['findings'])
    expected = 'blocked' if blocked else ('needs_input' if answer['questions'] else 'coherent')
    require(answer['verdict'] == expected and not (blocked and answer['questions']),
            'Consistency verdict contradicts findings/questions')
    return deepcopy(answer)


def _validate_location(item: dict, project: Path, change: dict, state: dict) -> None:
    if item['target'] == 'design':
        require(validate_plan_location(state['planning']['plan'], item['location']),
                'Invalid design plan location')
        return
    path = location_file(item['location'])
    if item['target'] == 'oracle':
        root = suite_relative(project, change['change_id'])
        require(bool(path and path.startswith(root + '/')), 'Oracle location is outside protected suite')
        # An old committed suite line cannot justify a missing current oracle reference.
        commit = None
    else:
        commit = state['contract_commit']
    require(evidence_location_exists(project, item['location'], commit),
            'Consistency evidence location does not exist')


def invalidate_consistency(state: dict, *, replan: bool = False) -> None:
    """Drop stale consistency without discarding valid characterization locks."""
    planning = state.get('planning', {})
    planning.pop('consistency', None)
    planning.pop('dependency_evidence', None)
    if replan:
        for key in ('plan', 'assessment', 'continue_heavy', 'pending_option'):
            planning.pop(key, None)


def _stale(project: Path, state: dict, error: StoryLoopError) -> dict:
    invalidate_consistency(state)
    state['planning']['plan'].pop('approval', None)
    state['planning'].pop('continue_heavy', None)
    state['planning'].pop('pending_option', None)
    return pause(project, state, 'DESIGN_REVISION_REQUIRED', phase='planning', reason=str(error))


def ensure_consistency(project: Path, change: dict, state: dict, services) -> dict | None:
    """Lock-owned feasibility and heavy consistency gate with live binding validation."""
    if not is_routed(change, state):
        return None
    try:
        binding = current_binding(project, change, state)
    except StoryLoopError as exc:
        return _stale(project, state, exc)
    plan = state['planning']['plan']
    if state['routing']['effective_tier'] == 'heavy' and not _valid_design(plan, binding):
        return pause(project, state, 'DESIGN_APPROVAL_REQUIRED', phase='planning', plan_id=plan['id'])
    from .dependency_checks import verify_dependencies
    try:
        dependencies = verify_dependencies(project, state, selected_option(plan)['dependencies'])
    except StoryLoopError as exc:
        if exc.code != 'NEEDS_INPUT':
            raise
        invalidate_consistency(state)
        return pause(project, state, 'NEEDS_INPUT', phase='planning', questions=[str(exc)])
    state['planning']['dependency_evidence'] = dependencies
    if state['routing']['effective_tier'] != 'heavy':
        save_run(project, state)
        return None
    binding = {**binding, 'plan_id': plan['id'], 'option_id': selected_option(plan)['id'],
               'answers_digest': digest(load_answers(project, change['change_id'])),
               'dependencies_digest': digest(dependencies)}
    cached = state['planning'].get('consistency')
    if cached and approval_matches(cached, binding):
        return _outcome(project, state, cached)
    state['planning'].pop('consistency', None)
    path = f"openspec/changes/{change['change_id']}/routing-plan.json"
    _write_artifact(project, path, plan)
    response = _review(project, change, state, services, path)
    record = {**response, 'binding': binding, 'status': consistency_status(response['findings'])}
    state['planning']['consistency'] = record
    return _outcome(project, state, record)



def _write_artifact(project: Path, relative: str, plan: dict) -> None:
    # Engine-owned control documents must be committed before workspace boundary audits.
    require(not changed_paths(project, '--cached'),
            'Routing artifact requires an unstaged index')
    write_json(safe_target(project, relative, 'routing plan artifact'), plan, max_bytes=2_000_000)
    if _git(project, 'status', '--porcelain', '--', relative).strip():
        commit_paths(project, [relative], 'docs(planning): record approved routing plan')


def _review(project: Path, change: dict, state: dict, services, path: str) -> dict:
    request = {'mode': 'consistency', 'change': deepcopy(change), 'plan_path': path,
               'plan': deepcopy(state['planning']['plan']), 'principles': load_principles(project),
               'dependency_evidence': deepcopy(state['planning']['dependency_evidence']),
               'answers': load_answers(project, change['change_id']),
               'oracle_path': suite_relative(project, change['change_id'])}
    response = answer_with_repair(
        lambda context: read_only_call(project, change['change_id'],
                                       lambda: services.review(project, context)),
        request, lambda value: validate_consistency(value, project, change, state))
    return validate_consistency(response, project, change, state)


def _outcome(project: Path, state: dict, record: dict) -> dict | None:
    if record['verdict'] == 'needs_input':
        return pause(project, state, 'NEEDS_INPUT', phase='planning', questions=record['questions'])
    findings = record['findings']
    status = consistency_status(findings)
    record['status'] = status
    if status == 'COHERENT':
        save_run(project, state)
        return None
    if status == 'DESIGN_REVISION_REQUIRED':
        return pause(project, state, status, phase='planning', findings=deepcopy(findings))
    from .project_loop_failures import _failure
    target = 'contract' if status == 'NEEDS_SPEC_FIX' else 'oracle'
    entry = {'attempt': state['attempt'], 'phase': 'planning', 'review': deepcopy(record)}
    failure = _failure(state, entry, target)
    if not state.get('failures') or state['failures'][-1] != failure:
        state.setdefault('failures', []).append(failure)
    blamed = [item for item in findings if item['target'] == target and item['blocking']]
    details = {'routed': {'source': 'consistency', 'target': target, 'findings': deepcopy(blamed)},
               'error': blamed[0]['message']}
    if target == 'oracle':
        details['acceptance'] = {'status': status, 'source': 'consistency', 'findings': deepcopy(blamed)}
    return pause(project, state, status, phase='planning', **details)


def overrule_consistency(project: Path, state: dict, reason: str) -> dict | None:
    """Reuse the explicit human overrule record, retaining all other consistency blockers."""
    from .project_loop_failures import _record_overrule, _clear_stop
    routed = state.pop('routed')
    keys = {finding_key(item) for item in routed['findings']}
    _record_overrule(state, reason, keys)
    record = state['planning']['consistency']
    for item in record['findings']:
        if item['target'] == routed['target'] and finding_key(item) in keys:
            item['blocking'] = False
    record['verdict'] = 'blocked' if consistency_status(record['findings']) != 'COHERENT' else 'coherent'
    _clear_stop(state)
    transition(project, state, "IMPLEMENTING", event="consistency_overrule")
    return _outcome(project, state, record)
