"""Replay scripted adapter replies; run with ``python -m ... --script FILE``.

Each call entry matches role, optional mode and optional one-based occurrence.
``creates`` is a list of {path, content} files relative to the request project.
This test utility never launches a model or a shell.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time

from storyloop.processutil import redact


def _safe(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_safe(item) for item in value]
    if isinstance(value, dict):
        sensitive = r'(?i)(api[_-]?key|access[_-]?token|password|token|secret|authorization|credentials)'
        return {redact(key): '[REDACTED]' if re.fullmatch(sensitive, key) else _safe(item)
                for key, item in value.items()}
    return value


def _record(script: Path, request: dict) -> int:
    role = request.get('role', 'capabilities')
    if not isinstance(role, str) or not re.fullmatch(r'[a-z][a-z0-9_-]*', role):
        raise ValueError('invalid request role')
    directory = script.parent / 'requests'
    if directory.is_symlink():
        raise ValueError('requests directory cannot be a symlink')
    directory.mkdir(exist_ok=True)
    records = sorted(directory.glob('[0-9]*-*.json'))
    sequence = max((int(path.name.split('-')[0]) for path in records), default=0) + 1
    while True:
        try:
            with (directory / f'{sequence:06d}-{role}.json').open('x', encoding='utf-8') as handle:
                json.dump(_safe(request), handle, ensure_ascii=False)
            break
        except FileExistsError:
            sequence += 1
    mode = (request.get('payload') or {}).get('mode')
    count = 1
    for path in directory.glob('[0-9]*-*.json'):
        if path.is_symlink() or int(path.name.split('-')[0]) >= sequence:
            continue
        previous = json.loads(path.read_text(encoding='utf-8'))
        if previous.get('role') == role and (previous.get('payload') or {}).get('mode') == mode:
            count += 1
    return count


def _create_files(request: dict, entries: list) -> None:
    root = Path(request.get('project') or Path.cwd()).resolve()
    for entry in entries:
        relative = Path(entry['path'])
        target = (root / relative).resolve()
        if relative.is_absolute() or '..' in relative.parts or not target.is_relative_to(root):
            raise ValueError('creates path must stay inside the project')
        if relative.parts and relative.parts[0] in {'.git', '.storyloop'}:
            raise ValueError('creates cannot modify control files')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(entry['content'], encoding='utf-8')


def _replay(script: Path, request: dict, scenario: dict) -> int:
    nth = _record(script, request)
    if request.get('protocol') == 'storyloop.adapter-capabilities.v1':
        print(json.dumps(scenario.get('capabilities', {})))
        return 0
    role, mode = request.get('role'), (request.get('payload') or {}).get('mode')
    call = next((item for item in scenario.get('calls', []) if item.get('role') == role
                 and ('mode' not in item or item['mode'] == mode)
                 and ('nth' not in item or item['nth'] == nth)), None)
    if call is None:
        print(redact(f'No scripted call for role={role}, mode={mode}, nth={nth}'), file=sys.stderr)
        return 3
    if call.get('sleep'):
        time.sleep(float(call['sleep']))
    _create_files(request, call.get('creates', []))
    result = dict(call.get('result', {}))
    if 'telemetry' in call:
        result['_telemetry'] = call['telemetry']
    print(call['stdout'] if 'stdout' in call else json.dumps(result, ensure_ascii=False))
    return int(call.get('exit', 0))


def main() -> int:
    """Replay one stdin request, recording it before any scripted failure."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--script', required=True, type=Path)
    args = parser.parse_args()
    try:
        return _replay(args.script.resolve(), json.load(sys.stdin),
                       json.loads(args.script.read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(redact(f'mock adapter: {error}'), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
