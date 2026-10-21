"""Signal readiness only after the containing supervisor has launched this child."""
from pathlib import Path
import subprocess
import sys


def main() -> int:
    try:
        child = subprocess.Popen(sys.argv[2:], shell=False)
    except (OSError, ValueError):
        return 2
    Path(sys.argv[1]).write_bytes(b"ready")
    return child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
