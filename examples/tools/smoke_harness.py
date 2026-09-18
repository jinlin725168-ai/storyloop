"""Reusable building blocks for StoryLoop smoke executors (standard library only).

A smoke command runs from the repository root of an isolated candidate copy with only PATH and
TMP in its environment, and must not write anything into that copy. Every stack therefore needs
an executor that (1) syncs the candidate into a scratch directory, (2) builds and boots there,
(3) runs the case files and (4) maps the outcome to StoryLoop's exit codes:
0 pass, 1 behavioural failure, 2 environment error.

Copy this file next to your executor (it has no dependencies) and compose it, e.g.::

    from smoke_harness import env_error, free_port, run_python_cases, split_cases, stop, sync_scratch, wait_http

    scratch = sync_scratch(Path.cwd(), "my-stack")
    ... build in `scratch`, start your server with start_new_session=True ...
    if not wait_http(server, f"http://127.0.0.1:{port}/health", 120):
        return env_error("server did not start", scratch / "server.log")
    return run_python_cases(py_cases, cwd=Path.cwd(), env={"SMOKE_BASE_URL": ...})
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

DEFAULT_BUILD_OUTPUT = {"target", "node_modules", "dist", "build", "logs", "__pycache__", ".venv"}

# Case files may live under acceptance/smoke/<change-id>/ whose name contains hyphens, so they
# are loaded by path instead of by dotted module name.
PATH_RUNNER = """
import importlib.util, sys, unittest
suite = unittest.TestSuite()
for path in sys.argv[1:]:
    name = path.replace('/', '_').replace('-', '_').replace('.', '_')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() and result.testsRun > 0 else 1)
"""


def split_cases(cases: list[str], root: Path, suffixes: tuple[str, ...]) -> dict[str, list[str]]:
    """Group case paths by suffix; raise ValueError for unknown suffixes or missing files."""
    groups: dict[str, list[str]] = {suffix: [] for suffix in suffixes}
    for case in cases:
        if not (root / case).is_file():
            raise ValueError(f"case file not found: {case}")
        for suffix in suffixes:
            if case.endswith(suffix):
                groups[suffix].append(case)
                break
        else:
            raise ValueError(f"unsupported case file: {case}")
    return groups


def sync_scratch(root: Path, name: str, build_output: set[str] | None = None) -> Path:
    """Mirror Git-visible files of `root` into a persistent scratch dir and return it.

    Build outputs already present in the scratch dir are preserved so incremental builds stay
    fast; files removed from the source are removed from the copy.
    """
    output = build_output or DEFAULT_BUILD_OUTPUT
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name)
    target = Path(tempfile.gettempdir()) / f"storyloop-{safe}"
    wanted = visible_files(root, output)
    keep = set(wanted)
    if target.exists():
        for directory, names, files in os.walk(target):
            names[:] = [item for item in names
                        if item not in output and not Path(directory, item).is_symlink()]
            for item in files:
                path = Path(directory, item)
                relative = path.relative_to(target).as_posix()
                if relative not in keep and not is_build_output(relative, output):
                    path.unlink()
    for relative in wanted:
        source, dest = root / relative, target / relative
        if dest.exists() and dest.stat().st_size == source.stat().st_size \
                and dest.stat().st_mtime >= source.stat().st_mtime:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
    return target


def visible_files(root: Path, build_output: set[str]) -> list[str]:
    """Git-visible files in a checkout, or every non-build file in a candidate copy (no .git)."""
    if (root / ".git").exists():
        raw = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=root,
                             capture_output=True, check=True).stdout
        return sorted(set(filter(None, raw.decode("utf-8").split("\0"))))
    files = []
    for directory, names, items in os.walk(root):
        names[:] = [item for item in names if item not in build_output and item != ".storyloop"]
        for item in items:
            relative = Path(directory, item).relative_to(root).as_posix()
            if not is_build_output(relative, build_output):
                files.append(relative)
    return sorted(files)


def is_build_output(relative: str, build_output: set[str]) -> bool:
    parts = relative.split("/")
    return any(part in build_output for part in parts) or relative.startswith("smoke-")


def run_python_cases(cases: list[str], cwd: Path, env: dict[str, str]) -> int:
    """Run unittest case files by path; returns 0 (pass) or 1 (failure)."""
    full = dict(os.environ)
    full.update(env)
    full["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run([sys.executable, "-B", "-c", PATH_RUNNER, *cases], cwd=cwd, env=full)
    return 0 if result.returncode == 0 else 1


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_http(process: subprocess.Popen, url: str, timeout: float) -> bool:
    """Poll `url` until any HTTP response arrives, the process exits, or `timeout` passes."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(urllib.request.Request(url), timeout=3):
                return True
        except urllib.error.HTTPError:
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    return False


def stop(process: subprocess.Popen) -> None:
    """Terminate a process started with start_new_session=True together with its group."""
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=30)
    except (ProcessLookupError, subprocess.TimeoutExpired, PermissionError):
        try:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
        except (ProcessLookupError, subprocess.TimeoutExpired, PermissionError):
            pass


def env_error(message: str, log: Path | None = None, tail: int = 80) -> int:
    """Report an environment failure (exit code 2) with the tail of a log when available."""
    print(f"smoke: {message}", file=sys.stderr)
    if log and log.is_file():
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-tail:]
        print("\n".join(lines), file=sys.stderr)
    return 2
