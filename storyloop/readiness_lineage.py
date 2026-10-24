"""Receipt-gated readiness lineage, using actual report results and unchanged inputs."""
from copy import deepcopy
import json
import shutil
import sys
from pathlib import Path
from .baseline import _git
from .models import StoryLoopError, require
from .project_profile import layer_commands
from .readiness import (PATH, LAYERS, COUNTED, _binding, _valid_cached, _record,
                        _record_digest, _summarize, _approved, readiness_status)
from .storage import load_json, write_json
from .system_smoke import load_system_smoke


def advance_readiness(project: Path, state: dict) -> dict | None:
    """Carry existing trust only across a verified receipt; otherwise leave stale cache intact."""
    if not (project / PATH).is_file() or not state.get('integration_commit'):
        return None
    try:
        previous = load_json(project / PATH)
        binding, config = _binding(project)
        old = previous['binding']
        require(previous['status'] == 'READY' and _valid_cached(previous, old), 'No trusted READY')
        require(old['source_commit'] == state['source_head'], 'Wrong readiness source')
        require(all(old[key] == binding[key] for key in old if key != 'source_commit'), 'Readiness inputs changed')
        report = _verified_report(project, state)
        layers = _layers(project, config, report['layers'])
        record = _certificate(previous, binding, layers, state)
    except (StoryLoopError, KeyError, TypeError, ValueError, OSError):
        return None
    evidence = Path('.storyloop/evidence/readiness') / (record['digest'] + '.json')
    write_json(project / evidence, {'version': 1, 'layers': layers, 'lineage': record['lineage']})
    for layer in record['layers'].values():
        layer['evidence_path'] = evidence.as_posix()
    write_json(project / PATH, record)
    return record


def _verified_report(project, state):
    from .change_report import merge_receipt_bytes
    from .project_receipt import authoritative_report, ensure_merge_receipt
    # Reverification on the already-existing receipt is read-only. Never create a receipt here.
    head = _git(project, 'rev-parse', 'HEAD').strip()
    require(head == state['integration_commit'] and head != state['merge_commit'], 'Receipt missing')
    bytes_to_check = merge_receipt_bytes(authoritative_report(project, state), state['merge_commit'])
    require(ensure_merge_receipt(project, state, bytes_to_check) == head, 'Receipt mismatch')
    path = Path(state['report']).relative_to(project).as_posix()
    report = json.loads(_git(project, 'show', f"{state['report_commit']}:{path}"))
    require(report['change_id'] == state['change_id'], 'Wrong report')
    return report


def _layers(project, config, supplied):
    manifest = load_system_smoke(project)
    collected = {}
    for name in LAYERS:
        commands = ([item['command'] for item in manifest['tests']] if manifest else []) if name == 'smoke' else layer_commands(config['profile'], name)
        layer = supplied[name]
        if not commands:
            require(layer.get('status') == 'SKIPPED' and not layer.get('results'), 'Missing layer mismatch')
            collected[name] = _summarize({'status': 'MISSING', 'results': []}, [], name)
            continue
        results = layer.get('results') or []
        require(layer.get('status') == 'PASSED' and len(results) == len(commands), 'Incomplete layer')
        for command, item in zip(commands, results):
            argv = [sys.executable if token == "{python}" else token for token in command]
            argv[0] = argv[0] if Path(argv[0]).is_absolute() else shutil.which(argv[0])
            require(item.get("command") == argv, "Command evidence mismatch")
            require(item.get('status') in {'PASS', 'PASSED'} and item.get('returncode') == 0
                    and item.get('timed_out') is False, 'Unproven command success')
            if name in COUNTED:
                counts = item.get('counts') or {}
                require(type(counts.get('executed')) is int and counts['executed'] > 0
                        and type(counts.get('failed')) is int and counts['failed'] == 0,
                        'Unproven execution counts')
        collected[name] = _summarize(layer, commands, name)
    return collected


def _certificate(previous, binding, layers, state):
    record = _record(binding, layers, None)
    require(record['gaps'] == previous['gaps'] and not record['blockers'], 'Readiness changed')
    record['approval'] = deepcopy(previous.get('approval'))
    record['lineage'] = {'previous_digest': previous['digest'],
                         'approval_digest': (previous.get('approval') or {}).get('digest'),
                         'receipt_commit': state['integration_commit'],
                         'report_commit': state['report_commit'], 'change_id': state['change_id']}
    record['digest'] = _record_digest(record)
    record['status'] = readiness_status(layers, _approved(record))
    require(record['status'] == 'READY', 'Cannot inherit readiness')
    return record
