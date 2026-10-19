"""Project protected execution argv into explicit, reproducible command references."""
from pathlib import Path


MAX_RESULTS = 200


def execution_commands(root, change_id, read_object, limit):
    """Do not substitute manifest templates for executed, protected command records."""
    path = root / '.storyloop/evidence' / str(change_id) / 'acceptance-result.json'
    value = read_object(root, path, limit)
    if value is None or not isinstance(value.get('results'), list):
        return None
    if len(value['results']) > MAX_RESULTS:
        return None
    records, ids = [], set()
    for result in value['results']:
        if not isinstance(result, dict) or result.get('status') not in ('PASS', 'FAIL', 'ERROR'):
            continue
        argv, test_id = result.get('command'), result.get('id')
        if (not isinstance(test_id, str) or test_id in ids or not isinstance(argv, list) or not argv
                or not all(isinstance(arg, str) and '\x00' not in arg for arg in argv)
                or any('{python}' in arg or '{project}' in arg for arg in argv)):
            return None
        ids.add(test_id)
        cwd = result.get('cwd') if isinstance(result.get('cwd'), str) else None
        replay = _replay(argv, cwd, root)
        if replay is None:
            return None
        records.append({'test_id': test_id, 'argv': replay, 'executed_argv': argv,
                        'cwd': str(root), 'execution_cwd': cwd,
                        'cwd_basis': 'PROJECT_ROOT_REPLAY',
                        'argv_projection': 'CANDIDATE_ROOT_TO_PROJECT' if replay != argv else 'UNCHANGED'})
    return records


def _replay(argv, cwd, root):
    if cwd is None or cwd == str(root):
        return argv
    projected = []
    for arg in argv:
        # Only an entire absolute path rooted at the candidate can be mapped safely.
        # Embedded paths in code/options cannot be rewritten without language parsing.
        if cwd in arg:
            path = Path(arg)
            if not path.is_absolute() or not path.is_relative_to(cwd):
                return None
            relative = path.relative_to(cwd)
            if '..' in relative.parts:
                return None
            projected.append(str(root / relative))
        else:
            projected.append(arg)
    return projected
