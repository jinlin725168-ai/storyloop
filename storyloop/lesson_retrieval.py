"""Select frozen repository lessons and separate claimed from unknown usage."""
from pathlib import Path

from .baseline import _git
from .lesson_store import effective_status, lesson_source_commit, load_lessons_at, stale_ids
from .lessons import scope_matches
from .models import StoryLoopError, identifier
from .storage import load_json

MAX_RELEVANT = 10
STRENGTH_ORDER = {'prose': 0, 'knowledge': 1, 'check': 2, 'config': 3}


def change_id(payload: dict) -> str | None:
    value = payload.get('change_id') or (payload.get('change') or {}).get('change_id')
    return value if isinstance(value, str) and value else None


def change_paths(project: Path, change_id: str | None) -> list[str]:
    """Read frozen routing scope and include changes already made on the feature branch."""
    if not change_id:
        return []
    path = project / '.storyloop/state/runs' / f'{identifier(change_id, "change id")}.json'
    if not path.is_file():
        return []
    state = load_json(path)
    routing = state.get('routing') or {}
    scope = routing.get('scope') or (routing.get('assessment') or {}).get('scope') or {}
    paths = list(scope.get('touched', [])) + list(scope.get('creates', []))
    if state.get('source_head') and state.get('feature_branch'):
        branch = f"refs/heads/{state['feature_branch']}"
        refs = _git(project, 'for-each-ref', '--format=%(refname)', branch).splitlines()
        if branch in refs:
            paths.extend(filter(None, _git(project, 'diff', '--name-only', '-z',
                              f"{state['source_head']}...{branch}", '--').split('\0')))
    return list(dict.fromkeys(paths))


def select_lessons(entries: list[dict], role: str, paths: list[str], stale: set[str]) -> dict:
    """Keep active core mandatory; filter and rank all other effective lessons."""
    core, relevant = [], []
    for entry in entries:
        status = effective_status(entry, stale)
        if status == 'retired':
            continue
        row = {'id': entry['id'], 'summary': entry['summary'],
               'path': f"openspec/lessons/{entry['id']}.md"}
        if entry['tier'] == 'core' and status == 'active':
            core.append({**row, 'body': entry['body']})
        elif role in entry['roles'] and (('**' in entry['scope']) if not paths else
                                        any(scope_matches(entry['scope'], p) for p in paths)):
            relevant.append((entry, {**row, 'status': status}))
    relevant.sort(key=lambda pair: pair[0]['updated_at'], reverse=True)
    relevant.sort(key=lambda pair: (pair[1]['status'] != 'active', STRENGTH_ORDER[pair[0]['strength']]))
    return {'core': core, 'relevant': [row for _, row in relevant[:MAX_RELEVANT]],
            'omitted': max(0, len(relevant) - MAX_RELEVANT)}


def lessons_payload(project: Path, role: str, payload: dict) -> tuple[dict | None, str | None]:
    """Skip an invalid layer atomically, keeping the call available."""
    try:
        source = lesson_source_commit(project, change_id(payload))
        entries = load_lessons_at(project, source)
        if not entries:
            return None, None
        return select_lessons(entries, role, change_paths(project, change_id(payload)),
                              stale_ids(project, entries, source)), None
    except StoryLoopError as exc:
        if exc.code != 'LESSON_INVALID':
            raise
        return None, f'LESSON_INVALID: {exc}'


def provided_ids(provided: dict | None) -> list[str]:
    """Return the full deduplicated delivered ID list, with core first."""
    return list(dict.fromkeys(row['id'] for key in ('core', 'relevant')
                             for row in (provided or {}).get(key, [])))


def claimed_lessons(provided: dict | None, response: dict) -> tuple[list[str], list[str]]:
    """Claims are agent assertions, not proof that a document was read."""
    supplied = set(provided_ids(provided))
    claims = list(dict.fromkeys(row['id'] for row in response.get('lessons_used', []) or []))
    return [item for item in claims if item in supplied], [item for item in claims if item not in supplied]
