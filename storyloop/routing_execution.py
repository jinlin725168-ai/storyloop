"""Ordered planning/approval/consistency/locks transitions for a routed execution."""
from copy import deepcopy

from .consistency import ensure_consistency
from .design_approval import ensure_design_approval
from .intake import digest
from .planning import ensure_routing_plan
from .project_loop_gates import ensure_characterization, ensure_design, resume_characterization
from .project_loop_support import pause, save_run
from .routing_restore import resume_restore, retire_restore
from .routing_characterization import candidate_manifest, verify_characterization_candidate
from .routing_state import is_routed
from .routing_upgrade import check_l3
from .smoke import smoke_digests
from .smoke_runner import implementation_digest


def prepare_execution(project, change, state, services, config):
    """Resume the persisted phase without charging an implementation attempt."""
    routed = is_routed(change, state)
    planning = state.setdefault('planning', {}) if routed else {}
    l3 = planning.get('origin') == 'L3'
    if l3 and planning.get('phase') == 'CHARACTERIZE' and not state.get('pending_characterization'):
        from .design_approval import current_binding
        current_binding(project, change, state)
        result = rebind_after_locks(project, change, state)
        if result is not None:
            return result
    if l3 and planning.get('phase') == 'REPLAN':
        resume_restore(project, state)
    gate = ensure_routing_plan if routed else ensure_design
    result = gate(project, change, state, services, config)
    if result is not None:
        return result
    for phase, action in [('APPROVAL', lambda: ensure_design_approval(project, change, state, config)),
                          ('CONSISTENCY', lambda: ensure_consistency(project, change, state, services))]:
        if l3:
            state['phase'] = 'planning'
            planning['phase'] = phase
            save_run(project, state)
        result = action()
        if result is not None:
            return result
    if l3:
        state['phase'] = 'planning'
        planning['phase'] = 'CHARACTERIZE'
        planning.setdefault('characterization_input', candidate_manifest(project))
        save_run(project, state)
    result = (resume_characterization(project, change, state, config)
              if state['status'] == 'CHARACTERIZATION_FAILED' else
              ensure_characterization(project, change, state, services, config))
    if result is not None:
        return result
    if l3:
        verify_characterization_candidate(project, state, config)
        result = rebind_after_locks(project, change, state)
        if result is not None:
            return result
        planning.pop('characterization_input', None)
        state.pop('phase', None)
        retire_restore(state)
        planning.update(phase='READY', execution_digest=implementation_digest(project))
        planning['processed_candidate'] = planning['candidate_digest']
        save_run(project, state)
    return None


def rebind_after_locks(project, change, state):
    """Locks may change approval inputs; preserve the checked plan and locks, require reapproval."""
    planning = state['planning']
    plan = planning['plan']
    live = {'candidate_digest': implementation_digest(project),
            'suite_digest': digest(smoke_digests(project, change['change_id']))}
    if planning.get('execution_digest') == live['candidate_digest']:
        return None
    if all(plan['binding'].get(k) == v for k, v in live.items()):
        return None
    planning.setdefault('history', []).append({'reason': 'Characterization changed binding',
                                               'plan': deepcopy(plan), 'attempt': state['attempt']})
    plan['binding'].update(live)
    plan.pop('approval', None)
    plan['id'] = digest({k: v for k, v in plan.items() if k not in {'id', 'status', 'approval'}})
    for key in ('consistency', 'dependency_evidence', 'continue_heavy', 'pending_option', 'execution_digest'):
        planning.pop(key, None)
    planning.pop('characterization_input', None)
    planning['phase'] = 'APPROVAL'
    return pause(project, state, 'DESIGN_APPROVAL_REQUIRED', phase='planning', plan_id=plan['id'])


def candidate_l3(project, change, state, services, config, boundary):
    """Return (replanned, pause) so a completed replan advances to the next implementation."""
    if check_l3(project, change, state, config) is None:
        checkpoint_candidate(project, state)
        return False, None
    return True, prepare_execution(project, change, state, services, config)


def checkpoint_candidate(project, state):
    """Record bytes produced by an authorized implement, never silently accept resume edits."""
    planning = state.get('planning', {})
    if planning.get('origin') == 'L3' and planning.get('phase') == 'READY':
        planning['execution_digest'] = implementation_digest(project)
        save_run(project, state)
