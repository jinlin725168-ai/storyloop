"""Trusted Windows bootstrap that waits until its Job Object is assigned."""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    if os.read(sys.stdin.fileno(), 1) != b"1" or len(sys.argv) < 2:
        return 125
    try:
        process = subprocess.Popen(sys.argv[1:], stdin=sys.stdin.buffer,
                                   stdout=sys.stdout.buffer, stderr=sys.stderr.buffer,
                                   shell=False)
    except (OSError, ValueError):
        print("Unable to start configured executable", file=sys.stderr)
        return 127
    try:
        return process.wait()
    except BaseException:
        process.kill()
        process.wait()
        raise


if __name__ == "__main__":
    raise SystemExit(main())

