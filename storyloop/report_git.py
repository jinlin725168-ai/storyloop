"""NUL-delimited, time/output-bounded Git facts for diagnostic snapshots."""
from pathlib import Path
import re
import time
import subprocess
import threading
import os
import tempfile

from .processutil import _start_process, _cleanup_process, _environment
from .git_command import GIT_ENVIRONMENT, git_process_policy


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
    prefix, _ = git_process_policy(root)
    return _capture(root, deadline, limit, ['git', *prefix, '--no-pager', *args])


def _capture(root, deadline, limit, command):
    remaining = deadline - time.monotonic()
    empty = {'stdout': '', 'returncode': None, 'limited': True}
    if remaining <= 0:
        return empty
    try:
        with tempfile.TemporaryFile() as source, open(os.devnull, 'wb') as error:
            call = _CaptureProcess(command, root, source, error)
            expired = threading.Event()
            timer, started = None, False
            try:
                timer = threading.Timer(max(0, deadline-time.monotonic()), _expire, args=(call, expired))
                timer.start()
                started = True
                # Keep pipe backpressure and the exact limit+1 read budget. Killing
                # only git.exe can leave its real Git child holding a Windows cwd.
                output = call.process.stdout.read(limit + 1)
                limited = len(output) > limit
                if limited:
                    call.finish()
                call.process.wait(timeout=max(.01, deadline-time.monotonic()))
                result = {'stdout': output[:limit].decode('utf-8'),
                          'returncode': call.process.returncode,
                          'limited': limited or expired.is_set()}
            finally:
                if timer is not None:
                    timer.cancel()
                stopped = call.finish()
                if started:
                    timer.join()
                call.process.stdout.close()
            return result if stopped else empty
    except (OSError, UnicodeError, RuntimeError, subprocess.TimeoutExpired):
        return empty


class _CaptureProcess:
    """Own the Git tree; deadline and output-budget cleanup share one finalizer."""
    def __init__(self, command, root, source, error):
        self._lock = threading.Lock()
        self._stopped = None
        if command[0] == 'git':
            _, policy = git_process_policy(root)
            environment = _environment(GIT_ENVIRONMENT, policy)
        else:
            environment = _environment(None, None)
        self.process, self.job, self.feeder = _start_process(
            command, root, environment, source, subprocess.PIPE, error)

    def finish(self):
        with self._lock:
            if self._stopped is None:
                self._stopped, _ = _cleanup_process(self.process, self.job, self.feeder)
            return self._stopped


def _expire(call, expired):
    expired.set()
    call.finish()
