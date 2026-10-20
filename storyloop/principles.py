"""Committed project principles, source binding and per-option coverage."""
import hashlib
import json
from pathlib import Path

from .baseline import _git, load_baseline
from .models import require, text
from .path_security import safe_target

PRINCIPLES_PATH = 'openspec/principles.json'


def load_principles(project: Path) -> dict:
    """Read only the fixed committed source and reject workspace or index drift."""
    path = safe_target(project, PRINCIPLES_PATH, 'principles source')
    source = load_baseline(project)['source_head']
    entry = _git(project, 'ls-tree', source, '--', PRINCIPLES_PATH).strip()
    require(_git(project, 'ls-tree', 'HEAD', '--', PRINCIPLES_PATH).strip() == entry,
            'Principles differ from the trusted baseline source; use BASE update')
    if not entry:
        require(not path.exists(), 'Principles must be committed before planning')
        return {'status': 'NOT_CONFIGURED', 'digest': None, 'principles': []}
    require(entry.split(None, 1)[0] == '100644', 'Principles must be a regular committed JSON file')
    raw = _git(project, 'show', source + ':' + PRINCIPLES_PATH)
    # Compare as Git does (clean conversion such as core.autocrlf), not raw checkout bytes.
    require(path.is_file() and _git(project, 'hash-object', '--', PRINCIPLES_PATH).strip()
            == entry.split(None, 3)[2], 'Principles source changed')
    require(not _git(project, 'diff', '--cached', '--', PRINCIPLES_PATH).strip(), 'Principles are staged')
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        require(False, 'Invalid principles JSON')
    require(isinstance(value, dict) and set(value) == {'version', 'principles'}
            and type(value['version']) is int and value['version'] == 1
            and isinstance(value['principles'], list), 'Invalid principles version or structure')
    seen = set()
    for item in value['principles']:
        require(isinstance(item, dict) and set(item) == {'id', 'text'}, 'Invalid principle')
        identity = text(item['id'], 'principle id')
        text(item['text'], 'principle text')
        require(identity == item['id'] and identity not in seen, 'Duplicate or noncanonical principle id')
        seen.add(identity)
    return {'status': 'CONFIGURED', 'digest': hashlib.sha256(raw.encode()).hexdigest(),
            'principles': value['principles']}


def check_principles(option: dict, source: dict) -> list[dict]:
    """Require exactly one declared verdict per committed principle; return deviations."""
    checks = option['principle_checks']
    expected = {item['id'] for item in source['principles']}
    ids = [item['principle_id'] for item in checks]
    require(len(ids) == len(set(ids)) and set(ids) == expected,
            'Plan principle_checks must cover exactly the committed principle IDs')
    for item in checks:
        require(item['verdict'] in {'complies', 'deviates', 'not_applicable', 'unknown'},
                'Invalid principle verdict')
        text(item['reason'], 'principle reason')
    return [dict(item) for item in checks if item['verdict'] == 'deviates']
