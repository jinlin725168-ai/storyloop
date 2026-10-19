"""NUL-delimited, time/output-bounded Git facts for diagnostic snapshots."""
from pathlib import Path
import re
import time
import subprocess
import threading


MAX_GIT_SECONDS = 10
COMMIT = re.compile(r'[0-9a-fA-F]{40,64}')


def git_snapshot(root: Path, run: dict | None, report: dict, limit: int, count: int) -> dict:
    """Read anchored path facts, marking any exhausted budget as incomplete."""
    base, head = (run or {}).get('source_head'), report.get('implementation_commit')
    deadline = time.monotonic() + MAX_GIT_SECONDS
    git = lambda *args: _git(root, deadline, limit, *args)
    if not (_commit(git, base) and _commit(git, head)):
        return {'base': None, 'head': head if _commit(git, head) else None,
                'files': [], 'availability': 'NOT_AVAILABLE'}
    result = git('diff', '--no-ext-diff', '--no-textconv', '--name-status', '-z', '-M', '-C', '--find-copies-harder', base, head, '--')
    files, incomplete = [], result['limited']
    if result['returncode'] == 0 and not incomplete:
        records = _records(result['stdout'])
        incomplete = len(records) > count
        for status, previous, path in records[:count]:
            item, limited = _file(git, base, head, status, previous, path)
            files.append(item)
            incomplete = incomplete or limited
            if limited:
                break
    return {'base': base, 'head': head, 'files': files,
            'availability': 'AVAILABLE' if result['returncode'] == 0 else 'NOT_AVAILABLE',
            'truncated': incomplete}


def _records(text):
    # -z separates both status/path and rename/copy source/destination with NUL.
    fields = iter(text.split('\x00')[:-1])
    records = []
    for status in fields:
        first = next(fields)
        previous = first if status.startswith(('R', 'C')) else None
        path = next(fields) if previous is not None else first
        records.append((status, previous, path))
    return records


def _file(git, base, head, status, previous, path):
    existed = git('cat-file', '-e', f'{base}:{previous or path}')
    raw = git('diff', '--no-ext-diff', '--no-textconv', '--quiet', base, head, '--', path)
    ignored = git('diff', '--no-ext-diff', '--no-textconv', '-w', '--quiet', base, head, '--', path)
    limited = any(result['limited'] for result in (existed, raw, ignored))
    return {'path': path, 'previous_path': previous, 'status': status,
            'existed': existed['returncode'] == 0,
            'whitespace_only': status.startswith('M') and raw['returncode'] == 1
                               and ignored['returncode'] == 0 and not limited}, limited


def _commit(git, value):
    return (isinstance(value, str) and COMMIT.fullmatch(value) is not None
            and git('cat-file', '-e', f'{value}^{{commit}}')['returncode'] == 0)


def _git(root, deadline, limit, *args):
    remaining = deadline - time.monotonic()
    empty = {'stdout': '', 'returncode': None, 'limited': True}
    if remaining <= 0:
        return empty
    try:
        with subprocess.Popen(['git', '--no-pager', *args], cwd=root, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
            expired = threading.Event()
            timer = threading.Timer(remaining, _expire, args=(process, expired))
            timer.start()
            try:
                # The pipe applies backpressure: retain at most limit+1 bytes, never
                # collect arbitrary output to memory or an unbounded temporary file.
                output = process.stdout.read(limit + 1)
                limited = len(output) > limit
                if limited:
                    _kill(process)
                process.wait(timeout=max(0.01, deadline - time.monotonic()))
                text = output[:limit].decode('utf-8')
                return {'stdout': text, 'returncode': process.returncode,
                        'limited': limited or expired.is_set()}
            finally:
                timer.cancel()
                _kill(process)
                timer.join()
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        return empty


def _kill(process):
    try:
        if process.poll() is None:
            process.kill()
    except ProcessLookupError:
        pass


def _expire(process, expired):
    expired.set()
    _kill(process)
