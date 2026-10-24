"""Explicit user decisions bound to the current routed design and assessment."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json

from .baseline import _git, load_baseline, load_project_config, verify_baseline
from .project_git import current_branch
from .changes import contract_digest, load_change
from .intake import digest
from .principles import load_principles
from .models import StoryLoopError, require, text
from .planning import assess_plan, _record_assessment
from .planning_validation import option_scope
from .project_loop_support import run_path, save_run
from .run_transitions import transition
from .run_stops import pause
from .routing_state import is_routed, run_config
from .smoke import smoke_digests
from .storage import load_json, locked

PLANNING_STOPS = {'DESIGN_APPROVAL_REQUIRED', 'DESIGN_REVISION_REQUIRED', 'SPLIT_RECOMMENDED'}


def approval_matches(approval: dict | None, binding: dict) -> bool:
    """Compare the entire canonical binding, preserving missing keys and JSON types."""
    return bool(isinstance(approval, dict) and isinstance(approval.get('binding'), dict)
                and json.dumps(approval['binding'], sort_keys=True, ensure_ascii=False,
                               separators=(',', ':'), allow_nan=False)
                == json.dumps(binding, sort_keys=True, ensure_ascii=False,
                              separators=(',', ':'), allow_nan=False))


def _validate_context(project: Path, change: dict, state: dict) -> None:
    require(is_routed(change, state), 'Design decisions require a routed run')
    require(change['status'] == 'APPROVED' and change.get('approval'), 'Contract is not approved')
    require(state['change_id'] == change['change_id'], 'Run identity differs')
    require(contract_digest(change['product']) == change['approval']['contract_digest']
            == state['contract_digest'], 'Approved contract changed')
    require(current_branch(project) == state['feature_branch'], 'Resume the saved StoryLoop branch')
    verify_baseline(project, resume=True)
    source = _git(project, 'rev-parse', state['source_branch']).strip()
    baseline = load_baseline(project)['source_head']
    require(source == baseline or _frozen_lesson_source(project, state, baseline, source),
            'Saved source branch moved outside a base upgrade')
    require(_git(project, 'merge-base', state['contract_commit'], 'HEAD').strip()
            == state['contract_commit'], 'Contract source is no longer an ancestor')
    require(digest(state['routing']['policy']) == state['routing']['policy_digest'],
            'Frozen routing policy changed')


def _frozen_lesson_source(project: Path, state: dict, baseline: str, source: str) -> bool:
    """Permit only lesson commits frozen before this run, never later source movement."""
    if source != state['source_head']:
        return False
    if _git(project, 'merge-base', baseline, source).strip() != baseline:
        return False
    paths = _git(project, 'diff', '--name-only', '-z', baseline, source).split('\0')
    return all(path.startswith('openspec/lessons/') for path in paths if path)


def current_binding(project: Path, change: dict, state: dict) -> dict:
    """Validate immutable plan identity and reconstruct its live input binding."""
    _validate_context(project, change, state)
    plan = state.get('planning', {}).get('plan')
    require(isinstance(plan, dict) and plan.get('status') == 'DONE', 'No completed routing plan')
    identity = {key: value for key, value in plan.items() if key not in {'id', 'status', 'approval'}}
    require(digest(identity) == plan['id'], 'Plan content or binding changed; replan required')
    binding = {'product_digest': digest(change['product']), 'source_commit': state['contract_commit'],
               'scope_digest': digest([{'id': o['id'], **option_scope(o)} for o in plan['options']]),
               'suite_digest': digest(smoke_digests(project, change)),
               'policy_digest': digest(state['routing']['policy']),
               'principles_digest': load_principles(project)['digest'],
               'baseline_digest': digest(load_baseline(project))}
    if 'candidate_digest' in plan['binding']:
        from .smoke_runner import implementation_digest
        actual = implementation_digest(project)
        checkpoint = state.get('planning', {}).get('execution_digest')
        if state['planning'].get('characterization_input') is not None:
            from .routing_characterization import verify_characterization_candidate
            verify_characterization_candidate(project, state, load_project_config(project))
            checkpoint = actual
        if checkpoint is not None:
            require(actual == checkpoint, 'Candidate changed outside authorized implementation')
        binding['candidate_digest'] = plan['binding']['candidate_digest'] if checkpoint else actual
    _assessment_plan(state)
    require(approval_matches({'binding': plan['binding']}, binding), 'Plan inputs changed; replan required')
    return binding


def _load(project: Path, change_id: str) -> tuple[dict, dict, dict]:
    change = load_change(project, change_id)
    state = load_json(run_path(project, change['change_id']))
    require(state.get('status') not in {'NEEDS_SPEC_FIX', 'ORACLE_DEFECT'},
            'Design decisions cannot replace contract/oracle repair; use the existing repair entry')
    config = run_config(load_project_config(project), change, state)
    return change, state, config


def _record(binding: dict, **fields) -> dict:
    return {'actor': 'user', 'time': datetime.now(timezone.utc).isoformat(),
            'binding': deepcopy(binding), **fields}


def _clear_checks(project: Path, state: dict) -> None:
    state['planning'].pop('consistency', None)
    state.pop('consistency', None)
    if not (state['planning'].get('origin') == 'L3'
            and state.get('characterization', {}).get('status') == 'GREEN'):
        state.pop('characterization', None)
    state.pop('pending_characterization', None)
    # Only this stop depends on the pending locks being cleared here.
    if state.get('status') == 'CHARACTERIZATION_FAILED':
        for key in ('stop', 'unit', 'error', 'next', 'questions'):
            state.pop(key, None)
        transition(project, state, 'DESIGN_APPROVAL_REQUIRED', event='design_reapprove', updates={'phase': 'planning'})


def _valid_design(plan: dict, binding: dict) -> bool:
    approval = plan.get('approval') or {}
    return (approval_matches(approval, binding) and approval.get('actor') == 'user'
            and approval.get('plan_id') == plan['id']
            and approval.get('option_id') in {o['id'] for o in plan['options']})


def approve_design(project: Path, change_id: str, plan_id: str, *, option_id: str | None = None) -> dict:
    """Record explicit design approval without executing any role or resuming the run."""
    project = Path(project).resolve()
    with locked(project):
        return _approve_design_locked(project, change_id, plan_id, option_id=option_id)


def _approve_design_locked(project: Path, change_id: str, plan_id: str, *,
                           option_id: str | None = None) -> dict:
    change, state, config = _load(project, change_id)
    binding = current_binding(project, change, state)
    plan = state['planning']['plan']
    require(plan_id == plan['id'], 'Stale plan id')
    selected = option_id if option_id is not None else plan['main_option']
    require(selected in {o['id'] for o in plan['options']}, 'Unknown plan option')
    old = plan.get('approval')
    plan['approval'] = _record(binding, plan_id=plan_id, option_id=selected, selected_option=selected)
    assessed = assess_plan(project, change, state, plan)
    require(not assessed['decision']['unresolved'], 'Selected plan has unresolved evidence')
    _record_assessment(state, config, assessed)
    if assessed['decision']['split'] and not _valid_continue(state, binding, assessed):
        plan.pop('approval', None)
        _clear_checks(project, state)
        state['planning'].pop('continue_heavy', None)
        state['planning']['pending_option'] = {'plan_id': plan_id, 'option_id': selected}
        return _pause_split(project, change, state, assessed)
    if old and _valid_design({**plan, 'approval': old}, binding) and old['option_id'] == selected:
        return deepcopy(old)
    if old:
        _clear_checks(project, state)
    state['planning'].pop('pending_option', None)
    save_run(project, state)
    return deepcopy(plan['approval'])


def _valid_continue(state: dict, binding: dict, assessed: dict) -> bool:
    approval = state['planning'].get('continue_heavy') or {}
    return (approval_matches(approval, binding) and approval.get('actor') == 'user'
            and approval.get('assessment_id') == assessed['id'])


def approve_continue_heavy(project: Path, change_id: str, assessment_id: str, reason: str) -> dict:
    """Accept one current split assessment, while retaining the separate design gate."""
    project = Path(project).resolve()
    with locked(project):
        return _approve_continue_heavy_locked(project, change_id, assessment_id, reason)


def _approve_continue_heavy_locked(project: Path, change_id: str, assessment_id: str, reason: str) -> dict:
    reason = text(reason, 'continue-heavy reason')
    change, state, config = _load(project, change_id)
    binding = current_binding(project, change, state)
    assessed = assess_plan(project, change, state, _assessment_plan(state))
    require(assessed['decision']['split'] and not assessed['decision']['unresolved'],
            'No complete split assessment to approve')
    require(assessment_id == assessed['id'], 'Stale split assessment')
    _record_assessment(state, config, assessed)
    if _valid_continue(state, binding, assessed):
        return deepcopy(state['planning']['continue_heavy'])
    record = _record(binding, assessment_id=assessment_id, reason=reason)
    state['planning']['continue_heavy'] = record
    save_run(project, state)
    return deepcopy(record)


def replan(project: Path, change_id: str, reason: str) -> dict:
    """Discard planning decisions at a planning stop, retaining contract and spent attempts."""
    project = Path(project).resolve()
    with locked(project):
        return _replan_locked(project, change_id, reason)


def _replan_locked(project: Path, change_id: str, reason: str) -> dict:
    reason = text(reason, 'replan reason')
    change, state, config = _load(project, change_id)
    _validate_context(project, change, state)
    planning_input = state['status'] == 'NEEDS_INPUT' and (
        state.get('phase') == 'planning' or state.get('stop', {}).get('phase') == 'planning')
    require(state['status'] in PLANNING_STOPS or planning_input, 'Replan requires a planning stop')
    _clear_checks(project, state)
    planning = state['planning']
    planning.setdefault('history', []).append({'reason': reason, 'plan': planning.get('plan'),
                                              'attempt': state.get('attempt', 0)})
    for key in ('plan', 'assessment', 'continue_heavy', 'split', 'pending_option'):
        planning.pop(key, None)
    if planning.get('origin') == 'L3':
        # Compatibility for older READY checkpoints that still carry a completed write intent.
        if (planning.get('execution_digest') and planning.get('processed_candidate')
                == planning.get('candidate_digest')):
            from .routing_restore import retire_restore
            retire_restore(state)
        planning['phase'] = 'REPLAN'
        planning.pop('execution_digest', None)
        planning.pop('characterization_input', None)
    # Explicit replan retires the old candidate's execution/review evidence.
    # Ordinary cost-stop resume still requires the original candidate digest.
    for key in ('cost_candidate', 'pending_review', 'review_disposition'):
        state.pop(key, None)
    state.pop('questions', None)
    state.pop('stop', None)
    transition(project, state, 'DESIGN_REVISION_REQUIRED', event='design_replan', updates={'phase': 'planning'})
    save_run(project, state)
    return deepcopy(state)


def _split_suggestions(change: dict) -> dict:
    # Story criteria alone cannot prove independence; surface proposals without inventing dependencies.
    return {'needs_human_decision': True,
            'reason': 'Confirm cross-story dependencies and independent acceptance before splitting.',
            'slices': [{'story_id': story['id'], 'criteria': [c['id'] for c in story['acceptance']],
                        'output': [c['then'] for c in story['acceptance']],
                        'dependencies': None, 'order': index + 1,
                        'independent_acceptance': 'requires human confirmation'}
                       for index, story in enumerate(change['product']['stories'])]}


def ensure_design_approval(project: Path, change: dict, state: dict, config: dict) -> dict | None:
    """Validate every DONE plan and enforce split then heavy approval before locks."""
    if not is_routed(change, state):
        return None
    state['phase'] = 'planning'
    try:
        binding = current_binding(project, change, state)
    except StoryLoopError as exc:
        state['planning']['plan'].pop('approval', None)
        state['planning'].pop('continue_heavy', None)
        state['planning'].pop('consistency', None)
        state['planning'].pop('pending_option', None)
        return pause(project, state, 'DESIGN_REVISION_REQUIRED', phase='planning', reason=str(exc))
    plan = state['planning']['plan']
    if plan.get('approval') and not _valid_design(plan, binding):
        plan.pop('approval')
        _clear_checks(project, state)
    assessed = assess_plan(project, change, state, _assessment_plan(state))
    _record_assessment(state, config, assessed)
    if assessed['decision']['unresolved']:
        state['phase'] = 'planning'
        return pause(project, state, 'NEEDS_INPUT', phase='planning', questions=[
            'Complete plan evidence: ' + ', '.join(assessed['decision']['unresolved'])])
    if assessed['decision']['split'] and not _valid_continue(state, binding, assessed):
        return _pause_split(project, change, state, assessed)
    if state['routing']['effective_tier'] == 'heavy' and not _valid_design(plan, binding):
        return pause(project, state, 'DESIGN_APPROVAL_REQUIRED', phase='planning', plan_id=plan['id'],
                     principle_deviations=deepcopy(assessed['principles']['deviations']))
    state.pop('phase', None)
    save_run(project, state)
    return None


def _assessment_plan(state: dict) -> dict:
    plan = state['planning']['plan']
    pending = state['planning'].get('pending_option')
    if not pending:
        return plan
    require(pending.get('plan_id') == plan['id'] and pending.get('option_id') in {
        o['id'] for o in plan['options']}, 'Stale pending option; replan required')
    projected = deepcopy(plan)
    projected['approval'] = {'option_id': pending['option_id']}
    return projected


def _pause_split(project: Path, change: dict, state: dict, assessed: dict) -> dict:
    state['phase'] = 'planning'
    state['planning']['split'] = _split_suggestions(change)
    return pause(project, state, 'SPLIT_RECOMMENDED', phase='planning', assessment_id=assessed['id'])
