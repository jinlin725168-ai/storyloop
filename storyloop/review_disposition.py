"""Review classification, semantic repair and resumable strong verification."""
from __future__ import annotations

import json

from .call_log import _redacted
from .finding_disposition import check_checklist, dispose
from .models import StoryLoopError, require
from .project_contracts import validate_review
from .project_loop_guards import read_only_call
from .project_loop_support import save_run
from .review_context import strong_verification
from .role_profiles import enabled, role_settings
from .role_schemas import schema_errors, schema_for
from .smoke_runner import implementation_digest


def _decisions(state, context):
    plan = state.get('planning', {}).get('plan') or {}
    if plan.get('status') != 'DONE' or not plan.get('approval'):
        return []
    constraints = (context.get('design_plan') or {}).get('constraints', [])
    return list(dict.fromkeys(json.dumps(row, sort_keys=True, ensure_ascii=False,
                                        separators=(',', ':')) for row in constraints))


def _call(project, change_id, services, context):
    return read_only_call(project, change_id, lambda: services.review(project, context))


def _answer(project, state, services, context, pending, decisions):
    if 'review' not in pending:
        pending['review'] = _redacted(_call(project, state['change_id'], services, context))
        save_run(project, state)
    try:
        _check_decisions(project, state, pending, decisions)
    except StoryLoopError as error:
        if pending.get('repaired'):
            raise
        request = {**context, 'previous_response': pending['review'],
                   'validation_errors': [str(error)]}
        try:
            pending['review'] = _redacted(_call(project, state['change_id'], services, request))
        except StoryLoopError as repair_error:
            if repair_error.code == 'AGENT_CONTRACT_INVALID':
                repair_error.details.update(review_semantic=True,
                                            checklist_missing=pending['checklist_missing'])
            raise
        pending['repaired'] = True
        save_run(project, state)
        _check_decisions(project, state, pending, decisions)
    validate_review(pending['review'])
    return pending['review']


def _check_decisions(project, state, pending, decisions):
    """Keep missing-decision evidence across repair errors and cost pauses."""
    missing = []
    try:
        check_checklist(pending['review'], decisions)
    except StoryLoopError as error:
        missing = error.details['checklist_missing']
        raise
    finally:
        pending['checklist_missing'] = missing
        state['review_disposition'] = {'attempt': state['attempt'],
                                      **dispose([], {}, []), 'checklist_missing': missing}
        save_run(project, state)


def _verify(project, state, services, context, pending, result):
    findings = result['fix'] + result['stop']
    if not findings or pending['tier_requested'] == 'strong':
        return
    request = {**context, 'mode': 'verify', 'findings': findings}
    with strong_verification():
        answer = _call(project, state['change_id'], services, request)
    errors = schema_errors(answer, schema_for('review', {'mode': 'verify'}))
    rows = answer.get('results', []) if isinstance(answer, dict) else []
    ids = [row.get('id') for row in rows if isinstance(row, dict)]
    if errors or len(ids) != len(findings) or len(ids) != len(set(ids)) or set(ids) != {item['id'] for item in findings}:
        raise StoryLoopError('Verification must answer each finding exactly once',
                             code='AGENT_CONTRACT_INVALID')
    confirmed = {row['id']: row for row in rows}
    for bucket in ('fix', 'stop'):
        retained = []
        for item in result[bucket]:
            item['verification'] = confirmed[item['id']]
            (retained if confirmed[item['id']]['confirmed'] else result['focus']).append(item)
        result[bucket] = retained


def perform_review(project, state, services, context, config):
    """Persist first review before verification so cost resume does not repeat it."""
    active = enabled(config)
    if not active:
        answer = _call(project, state['change_id'], services, context)
        validate_review(answer)
        if not any('confidence' in item for item in answer.get('findings', [])):
            return answer
        result = dispose(answer['findings'], config.get('review_thresholds', {}), [])
        result['verification'] = {'status': 'not_performed', 'reason': 'PROFILE_NOT_CONFIGURED'}
        return _finish(project, state, answer, result)
    decisions = _decisions(state, context)
    context = {**context, 'plan_decisions': decisions}
    binding = {'attempt': state['attempt'], 'digest': implementation_digest(project)}
    pending = state.get('pending_review')
    if pending is None:
        tier = role_settings(config, 'review', context.get('routing_tier'))['tier']
        pending = state['pending_review'] = {**binding, 'tier_requested': tier}
    require(all(pending.get(key) == value for key, value in binding.items()),
            'Pending review does not match the current candidate')
    answer = _answer(project, state, services, context, pending, decisions)
    result = dispose(answer['findings'], config.get('review_thresholds', {}), decisions)
    _verify(project, state, services, context, pending, result)
    return _finish(project, state, answer, result)


def _finish(project, state, answer, result):
    findings = result['fix'] + result['stop']
    verdict = 'fail' if findings else 'needs_input' if answer.get('questions') and not answer['findings'] else 'pass'
    state['review_disposition'] = {'attempt': state['attempt'], **_redacted(result)}
    state.pop('pending_review', None)
    save_run(project, state)
    return {**answer, 'verdict': verdict, 'findings': findings}
