"""Run a build or test command on a disposable copy of the Git-visible workspace.

StoryLoop treats any Git-ignored file that appears in the workspace during an agent call
(target/, dist/, node_modules/, caches) as a WORKSPACE_TAINTED violation. Agents therefore
never build in place; they call this helper, which copies tracked and untracked-but-not-ignored
files into a scratch directory, runs the command there and streams its output.

    python3 scratch_build.py [--keep] [--reuse NAME] -- mvn -q -o -f backend/pom.xml compile

--reuse NAME keeps the scratch directory between calls under $TMPDIR/storyloop-scratch-NAME so
incremental builds stay fast; files removed from the workspace are removed from the copy too.
Exit code is the command's exit code; 2 means the copy itself failed.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def _visible_files(root: Path) -> list[str]:
    output = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=root,
                            capture_output=True, check=True).stdout
    return sorted(set(filter(None, output.decode("utf-8").split("\0"))))


def _sync(root: Path, target: Path) -> None:
    wanted = _visible_files(root)
    if target.exists():
        keep = set(wanted)
        for path in sorted(target.rglob("*"), reverse=True):
            relative = path.relative_to(target).as_posix()
            if path.is_file() and relative not in keep and not _is_build_output(relative):
                path.unlink()
    for relative in wanted:
        source, dest = root / relative, target / relative
        if dest.exists() and dest.stat().st_mtime >= source.stat().st_mtime \
                and dest.stat().st_size == source.stat().st_size:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)


def _is_build_output(relative: str) -> bool:
    parts = relative.split("/")
    return any(part in {"target", "node_modules", "dist", "build", ".flattened-pom.xml"}
               or part.endswith(".flattened-pom.xml") for part in parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse", default=None, help="name of a persistent scratch directory")
    parser.add_argument("--keep", action="store_true", help="do not delete a temporary scratch dir")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = [item for item in args.command if item != "--"]
    if not command:
        print("scratch_build: no command given", file=sys.stderr)
        return 2
    root = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                               text=True, check=True).stdout.strip())
    if args.reuse:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in args.reuse)
        target = Path(tempfile.gettempdir()) / f"storyloop-scratch-{safe}"
        temporary = False
    else:
        target = Path(tempfile.mkdtemp(prefix="storyloop-scratch-"))
        temporary = not args.keep
    try:
        _sync(root, target)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"scratch_build: copy failed: {exc}", file=sys.stderr)
        return 2
    print(f"scratch_build: running in {target}", file=sys.stderr)
    try:
        return subprocess.run(command, cwd=target).returncode
    finally:
        if temporary:
            shutil.rmtree(target, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
