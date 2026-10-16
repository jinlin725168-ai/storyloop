"""Project test foundation gate, collected independently of changes and runs."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .baseline import _git, load_baseline, load_project_config
from .boundaries import capture_snapshot, snapshot_changes
from .models import StoryLoopError, require
from .processutil import redact
from .project_profile import layer_commands
from .smoke_runner import run_commands_at_commit
from .storage import load_json, locked, write_json
from .system_smoke import load_system_smoke, run_system_smoke_isolated, system_smoke_digests

PATH = Path('.storyloop/state/readiness.json')
LAYERS = ('unit', 'integration', 'checks', 'smoke')
COUNTED = ('unit', 'integration', 'smoke')


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _positive(value: object) -> bool:
    return type(value) is int and value > 0


def _blockers(layers: dict) -> list[str]:
    problems = [name for name in LAYERS if layers.get(name, {}).get('status')
                not in {'PASSED', 'MISSING'}]
    problems.extend(name for name in COUNTED if layers.get(name, {}).get('status') == 'PASSED'
                    and (not _positive(layers[name].get('executed'))
                         or _positive(layers[name].get('failed'))))
    if not any(layers.get(name, {}).get('status') == 'PASSED'
               and _positive(layers[name].get('executed')) for name in COUNTED):
        problems.append('NO_EXECUTED_TESTS')
    return sorted(set(problems))


def readiness_status(layers: dict, approved_gaps: list[str]) -> str:
    """Decide readiness from actual execution; approval never waives failed layers."""
    if _blockers(layers):
        return 'PROJECT_NOT_READY'
    if any(layers[name]['status'] == 'MISSING' and name not in approved_gaps
           for name in ('checks', 'smoke')):
        return 'READINESS_APPROVAL_REQUIRED'
    return 'READY'


def _binding(project: Path) -> tuple[dict, dict]:
    require(not _git(project, 'status', '--porcelain=v1', '--untracked-files=all').strip(),
            'Readiness requires a clean committed source tree')
    config = load_project_config(project)
    suite = load_system_smoke(project)
    binding = {'baseline_version': load_baseline(project)['baseline_version'],
               'source_commit': _git(project, 'rev-parse', 'HEAD').strip(),
               'test_config_digest': _digest(config),
               'suite_digests': system_smoke_digests(project) if suite else {}}
    return binding, config


def inspect_readiness(project: Path, *, refresh: bool = False) -> dict:
    """Collect or reuse readiness under the project lock, without creating a run."""
    project = Path(project).resolve()
    with locked(project):
        return _inspect_readiness_locked(project, refresh=refresh)


def _inspect_readiness_locked(project: Path, *, refresh: bool = False) -> dict:
    """Inspect readiness when the caller already owns the non-reentrant lock."""
    project = Path(project).resolve()
    binding, config = _binding(project)
    previous = load_json(project / PATH) if (project / PATH).is_file() else None
    if not refresh and previous and _valid_cached(previous, binding):
        return previous
    layers = _collect_guarded(project, binding, config)
    record = _record(binding, layers, previous)
    evidence = Path('.storyloop/evidence/readiness') / (record['digest'] + '.json')
    write_json(project / evidence, {'version': 1, 'layers': layers}, max_bytes=2_000_000)
    for layer in record['layers'].values():
        layer['evidence_path'] = evidence.as_posix()
    write_json(project / PATH, record, max_bytes=2_000_000)
    return record


def _record_digest(record: dict) -> str:
    layers = {name: {key: value for key, value in layer.items() if key != 'evidence_path'}
              for name, layer in record['layers'].items()}
    content = {'version': 1, 'binding': record['binding'], 'layers': layers,
               'gaps': record['gaps'], 'blockers': record['blockers']}
    if record.get('lineage'):
        content['lineage'] = record['lineage']
    return _digest(content)


def _valid_cached(record: dict, binding: dict) -> bool:
    try:
        return (record['version'] == 1 and record['binding'] == binding
                and set(record['layers']) == set(LAYERS)
                and record['digest'] == _record_digest(record)
                and record['status'] == readiness_status(record['layers'], _approved(record)))
    except (KeyError, TypeError, ValueError):
        return False


def _approved(record: dict) -> list[str]:
    approval = record.get('approval') or {}
    lineage = record.get('lineage') or {}
    inherited = (lineage.get('approval_digest') == approval.get('digest')
                 and all(lineage.get(key) for key in ('previous_digest', 'receipt_commit', 'report_commit')))
    if ((approval.get('digest') == record['digest'] or inherited) and approval.get('gaps') == record['gaps']
            and isinstance(approval.get('reason'), str) and approval['reason'].strip()):
        return record['gaps']
    return []


def _record(binding: dict, layers: dict, previous: dict | None) -> dict:
    record = {'version': 1, 'binding': binding, 'layers': layers,
              'gaps': [name for name in ('checks', 'smoke') if layers[name]['status'] == 'MISSING'],
              'blockers': _blockers(layers), 'approval': None}
    record['digest'] = _record_digest(record)
    if previous and previous.get('digest') == record['digest'] and _approved(previous):
        record['approval'] = previous['approval']
    record['status'] = readiness_status(layers, _approved(record))
    return record


def _collect_guarded(project: Path, binding: dict, config: dict) -> dict:
    staged = _git(project, 'diff', '--cached', '--name-status')
    before = capture_snapshot(project, include_control=True, include_ignored=True)
    control = capture_snapshot(project / '.storyloop', include_control=True, include_ignored=True)
    try:
        return {name: _collect_layer(project, binding, config, name) for name in LAYERS}
    finally:
        after = capture_snapshot(project, include_control=True, include_ignored=True)
        control_after = capture_snapshot(project / '.storyloop', include_control=True, include_ignored=True)
        if snapshot_changes(before, after) or snapshot_changes(control, control_after):
            raise StoryLoopError('Readiness modified the source or control plane',
                                 code='WORKSPACE_TAINTED')
        current, _ = _binding(project)
        if current != binding or _git(project, 'diff', '--cached', '--name-status') != staged:
            raise StoryLoopError('Readiness inputs changed during collection', code='WORKSPACE_TAINTED')


def _collect_layer(project: Path, binding: dict, config: dict, name: str) -> dict:
    manifest = load_system_smoke(project) if name == 'smoke' else None
    commands = ([item['command'] for item in manifest['tests']] if manifest else []) if name == 'smoke' else (
        layer_commands(config['profile'], name))
    if not commands:
        return _summarize({'status': 'MISSING', 'results': []}, commands, name)
    try:
        outcome = (run_system_smoke_isolated(project) if name == 'smoke' else run_commands_at_commit(
            project, binding['source_commit'], commands, config['check_timeout_seconds']))
    except StoryLoopError as exc:
        if exc.code == 'WORKSPACE_TAINTED':
            raise
        outcome = {'status': 'ERROR', 'results': [], 'error_code': exc.code}
    return _summarize(outcome, commands, name)


def _summarize(outcome: dict, commands: list, name: str) -> dict:
    results = outcome.get('results') or []
    counts = [item.get('counts') or {} for item in results]
    summary = {'status': outcome['status'], 'command_fingerprint': _digest(commands),
               'discovered': None, 'executed': None, 'failed': None, 'skipped': None,
               'results': [_result(item) for item in results]}
    for key in ('executed', 'failed', 'skipped'):
        values = [item.get(key) for item in counts]
        if values and all(type(value) is int and value >= 0 for value in values):
            summary[key] = sum(values)
    if name in COUNTED and summary['status'] == 'PASSED':
        if len(results) != len(commands) or any(not _positive(item.get('executed'))
                                              or _positive(item.get('failed')) for item in counts):
            summary['status'] = 'FAILED'
            summary['reason'] = 'EXECUTION_NOT_PROVEN'
    if outcome.get('error_code'):
        summary['error_code'] = outcome['error_code']
    return summary


def _result(item: dict) -> dict:
    # Keep only diagnostic metadata: arbitrary stdout/stderr never enters readiness state.
    keys = ('id', 'status', 'returncode', 'timed_out', 'counts')
    return {key: item.get(key) for key in keys}


def require_readiness(project: Path) -> dict:
    """Return READY evidence or raise its readiness status with the complete record."""
    project = Path(project).resolve()
    with locked(project):
        return _require_readiness_locked(project)


def _require_readiness_locked(project: Path) -> dict:
    """Require readiness without reacquiring a caller's project lock."""
    record = _inspect_readiness_locked(project)
    if record['status'] != 'READY':
        raise StoryLoopError('Project test foundation is not ready; establish independent '
                             'characterization tests and review missing checks/system smoke.',
                             code=record['status'], details=record)
    return record


def approve_readiness(project: Path, digest: str, reason: str) -> dict:
    """Approve exactly the current missing layers after recollecting bound evidence."""
    require(isinstance(reason, str) and bool(reason.strip()), 'Readiness approval needs a reason')
    project = Path(project).resolve()
    with locked(project):
        record = _inspect_readiness_locked(project, refresh=True)
        require(digest == record['digest'], 'Readiness digest is stale')
        require(record['status'] != 'PROJECT_NOT_READY', 'Failed readiness cannot be approved')
        if record['approval']:
            return record
        require(bool(record['gaps']), 'Readiness has no gaps to approve')
        record['approval'] = {'digest': digest, 'gaps': list(record['gaps']),
                              'reason': redact(reason.strip()), 'actor': 'human',
                              'time': datetime.now(timezone.utc).isoformat()}
        record['status'] = readiness_status(record['layers'], record['gaps'])
        write_json(project / PATH, record, max_bytes=2_000_000)
        return record
