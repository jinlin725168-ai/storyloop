"""Evidence-backed coverage declarations for routed characterization gates."""
import hashlib
from pathlib import Path

from .models import require
from .project_loop_guards import exists_at
from .path_security import repository_relative, safe_target
from .project_profile import matches_test_root


def _artifact(project: Path, path: str, config: dict) -> str:
    relative = repository_relative(path, 'characterization artifact')
    require(matches_test_root(relative, config['profile'].get('test_roots', [])),
            'Characterization artifact must be inside test_roots')
    target = safe_target(project, relative, 'characterization artifact')
    require(target.is_file() and target.stat().st_size > 0,
            f'Characterization artifact is missing or empty: {relative}')
    return hashlib.sha256(target.read_bytes()).hexdigest()


def verified_locks(project: Path, state: dict, config: dict) -> dict:
    """Reuse only recorded GREEN coverage whose protected test artifacts still match."""
    previous = state.get('characterization') or {}
    if previous.get('status') != 'GREEN':
        return {}
    locks = previous.get('locks', {})
    for path, artifacts in locks.items():
        require(bool(artifacts), f'Characterization coverage has no artifacts: {path}')
        for test, expected in artifacts.items():
            require(_artifact(project, test, config) == expected,
                    f'Characterization artifact changed since GREEN: {test}')
    return locks


def routed_coverage(project: Path, state: dict, config: dict, response: dict,
                    changed: list[str], required: list[str]) -> dict:
    """Require explicit path coverage and real artifacts, never infer coverage from a plan."""
    previous = verified_locks(project, state, config)
    declared = set(response['touched'])
    require(set(required) <= declared | set(previous),
            'Characterization coverage declaration omits required paths')
    tests = sorted(set(response['tests']) | set(changed))
    for test in tests:
        _artifact(project, test, config)
        require(test in changed or exists_at(project, 'HEAD', test),
                'Characterization artifact must be tracked or produced by this call')
    produced = set(tests) & set(changed)
    require(not declared - set(previous) or bool(produced),
            'New characterization coverage requires artifacts produced by this call')
    coverage = {path: list(artifacts) for path, artifacts in previous.items()}
    for path in declared:
        coverage[path] = sorted(set(coverage.get(path, [])) | set(tests))
    return coverage


def capture_locks(project: Path, config: dict, pending: dict) -> dict:
    """Verify artifacts again on resume and capture the exact bytes tested before GREEN."""
    coverage = pending.get('coverage')
    require(isinstance(coverage, dict), 'Routed characterization lacks coverage evidence')
    require(set(pending['touched']) <= set(coverage), 'Characterization coverage is incomplete')
    locks = {}
    for path, tests in coverage.items():
        require(bool(tests), f'Characterization coverage has no artifacts: {path}')
        locks[path] = {test: _artifact(project, test, config) for test in tests}
    return locks


def require_saved_coverage(project: Path, state: dict, config: dict, required: list[str]) -> None:
    """A cached GREEN result does not exempt newly required paths from evidence checks."""
    require(set(required) <= set(verified_locks(project, state, config)),
            'Characterization coverage evidence is missing for required paths')
