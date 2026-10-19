"""Fingerprint non-lock candidate bytes across a crash during characterization."""
from .characterization_locks import verified_locks
from .models import StoryLoopError
from .project_profile import GENERATED_PATHS
from .routing_restore import fingerprint
from .smoke_runner import visible_paths


def candidate_manifest(project):
    """Content-free fingerprints for all non-generated, Git-visible candidate paths."""
    return {path: fingerprint(project, path) for path in visible_paths(project)
            if not any(path.startswith(root + '/') for root in GENERATED_PATHS)}


def verify_characterization_candidate(project, state, config):
    """Only declared pending or verified lock artifacts may differ from the pre-lock candidate."""
    before = state['planning']['characterization_input']
    current = candidate_manifest(project)
    pending = state.get('pending_characterization')
    if pending:
        from .project_profile import matches_test_root
        allowed = set(pending['tests']) | set(pending['changed'])
        if not all(matches_test_root(p, config['profile'].get('test_roots', [])) for p in allowed):
            raise StoryLoopError('Invalid pending characterization paths')
    else:
        locks = verified_locks(project, state, config)
        allowed = {path for artifacts in locks.values() for path in artifacts}
    changed = {p for p in set(before) | set(current) if before.get(p) != current.get(p)}
    if changed - allowed:
        raise StoryLoopError('Candidate changed outside characterization: ' + ', '.join(sorted(changed - allowed)),
                             code='WORKSPACE_TAINTED')
