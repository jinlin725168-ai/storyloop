"""Write-ahead, path-specific L3 restoration without copying source into control logs."""
from pathlib import Path

from .baseline import _git
from .models import StoryLoopError, require
from .path_security import repository_relative, safe_target
from .project_loop_support import save_run


def fingerprint(project: Path, relative: str) -> dict:
    """Fingerprint ordinary candidate bytes and executable mode; reject links and directories."""
    path = safe_target(project, relative, 'restore path')
    if not path.exists():
        return {'blob': None, 'mode': None}
    require(path.is_file() and not path.is_symlink(), 'Unsafe restore path')
    # Hash as Git would store it so a core.autocrlf checkout of the contract blob matches it.
    return {'blob': _git(project, 'hash-object', '--', relative).strip(),
            'mode': '100755' if path.stat().st_mode & 0o111 else '100644'}


def prepare_restore(project: Path, state: dict, paths: list[str]) -> None:
    """Atomically persist the entire recovery intent before the first source write."""
    entries = []
    for raw in sorted(set(paths)):
        path = repository_relative(raw, 'restore path')
        before = fingerprint(project, path)
        tree = _git(project, 'ls-tree', state['contract_commit'], '--', path).strip().split()
        require(len(tree) >= 4 and tree[0] in {'100644', '100755'},
                'Restore source must be an existing regular contract file')
        entries.append({'path': path, 'before': before,
                        'contract': {'mode': tree[0], 'blob': tree[2]}, 'done': False})
    state['planning']['restore'] = {'phase': 'PENDING', 'source': state['contract_commit'],
                                    'reason': 'Lock existing behavior before implementation',
                                    'paths': entries}
    save_run(project, state)


def resume_restore(project: Path, state: dict) -> None:
    """Resume any write window, accepting only the recorded candidate or restored contract."""
    restore = state.get('planning', {}).get('restore')
    if not restore:
        return
    require(restore['source'] == state['contract_commit'], 'Restore source changed')
    for entry in restore['paths']:
        actual = fingerprint(project, entry['path'])
        allowed = [entry['contract']] if entry['done'] else [entry['before'], entry['contract']]
        if actual not in allowed:
            raise StoryLoopError('Restore conflict: candidate path changed: ' + entry['path'],
                                 code='WORKSPACE_TAINTED')
    for entry in restore['paths']:
        if fingerprint(project, entry['path']) != entry['contract']:
            _git(project, 'checkout', restore['source'], '--', entry['path'])
        require(fingerprint(project, entry['path']) == entry['contract'], 'Restored bytes changed')
        entry['done'] = True
        save_run(project, state)
    restore['phase'] = 'DONE'
    save_run(project, state)


def retire_restore(state: dict) -> None:
    """Retire a completed write intent after all gates; keep its mandatory locks and audit history."""
    planning = state['planning']
    restore = planning.get('restore')
    if not restore:
        return
    require(restore['phase'] == 'DONE' and all(entry['done'] for entry in restore['paths']),
            'Cannot retire pending restore intent')
    planning.setdefault('restore_history', []).append({**restore, 'retired_attempt': state['attempt']})
    planning.pop('restore')


def restore_lock_paths(state: dict) -> set[str]:
    """Restoring source requires coverage even after the write intent has retired."""
    planning = state.get('planning', {})
    restores = [*planning.get('restore_history', []), planning.get('restore', {})]
    return {entry['path'] for restore in restores for entry in restore.get('paths', [])}
