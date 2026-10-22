"""Persist candidate evidence across a cost stop without spending a quality attempt."""
from __future__ import annotations

from dataclasses import asdict
from .boundaries import FileChange
from .call_log import _redacted
from .models import require
from .project_loop_support import save_run
from .run_stops import pause
from .run_transitions import transition
from .smoke_runner import implementation_digest


def checkpoint(project, state, response, touched):
    """Retain an answered implementation so review can resume without rewriting it."""
    state['cost_candidate'] = {'response': _redacted(response), 'touched': [asdict(item) for item in touched],
                               'digest': implementation_digest(project)}
    save_run(project, state)


def candidate(project, state):
    """Return a saved candidate only if its bytes still match the authorized output."""
    saved = state.get('cost_candidate')
    if saved is None:
        return None
    require(saved['digest'] == implementation_digest(project),
            'Workspace changed while cost stop was paused; restore the saved candidate before run')
    return saved['response'], [FileChange(**item) for item in saved['touched']]


def stop(project, state, error, in_attempt=False):
    """Refund only an interrupted attempt and remember the prior preparation status."""
    state['cost_resume_status'] = state['status']
    if in_attempt:
        consumed = state.get('planning', {}).get('consumed_attempt', 0)
        if consumed >= state['attempt']:
            # L3 already judged and consumed this candidate; its own restore/plan state owns recovery.
            state.pop('cost_candidate', None)
        state['attempt'] = max(state['attempt'] - 1, consumed)
    state['cost_resume_digest'] = implementation_digest(project)
    return pause(project, state, 'COST_LIMIT_REACHED', **error.details)


def restore(project, state):
    """Restore preparation or execution phase after validating the retained candidate."""
    if state['status'] != 'COST_LIMIT_REACHED':
        return
    require(state.get('cost_resume_digest') == implementation_digest(project),
            'Workspace changed while cost stop was paused; restore the saved candidate before run')
    transition(project, state, state['cost_resume_status'], event='cost_restore',
               effect=lambda: _pop_resume_status(state))
    state.pop('cost_resume_digest', None)
    for key in ('used', 'limits', 'by_role', 'cost_coverage'):
        state.pop(key, None)


def _pop_resume_status(state):
    state.pop('cost_resume_status')
    return {}
