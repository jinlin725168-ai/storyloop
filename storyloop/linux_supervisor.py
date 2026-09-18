"""Linux child subreaper; acknowledge completion only after reaping every child.

This manages trusted commands, not hostile processes running as the same user.
It runs separately so concurrent calls cannot adopt one another's descendants.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


class StopRequested(BaseException):
    pass


def _stop(signum, frame):
    raise StopRequested()


def _reap_all() -> None:
    deadline = time.monotonic() + 8
    children = Path(f"/proc/self/task/{os.getpid()}/children")
    while True:
        try:
            while os.waitpid(-1, os.WNOHANG)[0]:
                pass
        except ChildProcessError:
            return
        for pid in children.read_text().split():
            try:
                os.kill(int(pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        if time.monotonic() >= deadline:
            raise TimeoutError("Linux process tree did not become empty")
        time.sleep(0.01)


def main() -> int:
    completion = int(sys.argv[1])
    os.set_inheritable(completion, False)
    # Do not launch anything unless adoption and /proc enumeration are available.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        return 125
    Path(f"/proc/self/task/{os.getpid()}/children").read_text()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    code = 125
    try:
        process = subprocess.Popen(sys.argv[2:], close_fds=True)
        code = process.wait()
    except StopRequested:
        code = 124
    except (OSError, ValueError):
        print("Unable to start configured executable", file=sys.stderr)
        code = 127
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        _reap_all()
    os.write(completion, b"1")
    os.close(completion)
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    raise SystemExit(main())
