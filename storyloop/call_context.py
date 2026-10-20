"""Record only explicitly delivered context references, never document bodies."""
import hashlib
from pathlib import Path, PurePosixPath

from .models import StoryLoopError
from .path_security import safe_target
from .storage import load_json


def capability_for(project: Path, adapter: str) -> dict:
    """Use the already-validated handshake; a missing capability remains unknown."""
    try:
        return load_json(project / '.storyloop/state/adapters.json').get('adapters', {}).get(adapter, {})
    except StoryLoopError:
        return {}


def context_manifest(project: Path, payload: dict, skills: list[dict]) -> dict:
    """Hash allowlisted supplied references; do not scan the repository to invent context."""
    result = {'foundation': [], 'living_specs': [], 'skills': [
        {'path': s['path'], 'sha256': s['sha256']} for s in skills], 'plans': [], 'lessons': [], 'truncated': []}
    foundation = payload.get('foundation') or {}
    for value in foundation.get('documents', []) if isinstance(foundation, dict) else []:
        _reference(project, value, 'openspec/foundation/', result, 'foundation')
    # These optional fields are explicit delivery lists; arbitrary payload strings are never read.
    for key, prefix, destination in [('living_specs', 'openspec/specs/', 'living_specs'),
                                     ('plan_files', 'openspec/changes/', 'plans')]:
        rows = payload.get(key, [])
        for value in rows if isinstance(rows, list) else []:
            _reference(project, value, prefix, result, destination)
    _lesson_references(project, payload, result)
    return result


def _reference(project: Path, value: object, prefix: str, result: dict, key: str) -> None:
    path = value.get('path') if isinstance(value, dict) else value
    if not isinstance(path, str) or not path.startswith(prefix) or not path.endswith(('.md', '.json')):
        return
    try:
        target = safe_target(project, path, 'context reference')
        if isinstance(value, dict) and isinstance(value.get('content'), str):
            body = value['content'].encode('utf-8')
        else:
            if not target.is_file():
                return
            body = target.read_bytes()
    except (OSError, StoryLoopError):
        return
    result[key].append({'path': path, 'sha256': hashlib.sha256(body).hexdigest()})
    if isinstance(value, dict) and value.get('truncated') is True:
        result['truncated'].append(path)


def _lesson_references(project: Path, payload: dict, result: dict) -> None:
    from .baseline import _git
    from .lesson_retrieval import change_id
    from .lesson_store import lesson_source_commit, _resolve_commit, _tree_record
    lessons = payload.get('lessons') or {}
    if not lessons:
        return
    retrospective = payload.get('mode') == 'retrospective'
    source = _resolve_commit(project, 'HEAD' if retrospective else lesson_source_commit(project, change_id(payload)))
    if retrospective:
        lessons = {'relevant': lessons}
    for key in ('core', 'relevant'):
        for row in lessons.get(key, []):
            path = row.get('path')
            if (not isinstance(path, str) or not path.startswith('openspec/lessons/')
                    or PurePosixPath(path).parent.as_posix() != 'openspec/lessons'):
                continue
            try:
                record = _tree_record(project, source, path)
                if record is None or record[0] not in {'100644', '100755'} or record[1] != 'blob':
                    continue
                body = _git(project, 'show', record[2]).encode('utf-8')
                result['lessons'].append({'path': path, 'sha256': hashlib.sha256(body).hexdigest()})
            except StoryLoopError:
                continue
