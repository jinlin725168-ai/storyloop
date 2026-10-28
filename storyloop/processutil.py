"""Bounded subprocess execution without shell interpolation."""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from .models import StoryLoopError

MAX_OUTPUT = 1024 * 1024
SAFE_ENV = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE",
            "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "COMSPEC", "JAVA_HOME"}
SENSITIVE_ENV_MARKERS = ("KEY", "TOKEN", "PASSWORD", "SECRET", "CREDENTIAL")


def redact(value: str) -> str:
    """Remove common credential forms before persisting or displaying output."""
    value = re.sub(r"(?i)(password|token|secret|authorization|api[_-]?key)\s*[:=]\s*[^\s,;]+",
                   r"\1=[REDACTED]", value)
    def hide_userinfo(match: re.Match[str]) -> str:
        userinfo = match.group(1)
        scheme_start = match.start()
        while scheme_start and value[scheme_start - 1] in "abcdefghijklmnopqrstuvwxyz0123456789+.-":
            scheme_start -= 1
        scheme = value[scheme_start:match.start()]
        colon = userinfo.find(":", 1)
        return ("://[REDACTED]@" if any("a" <= char <= "z" for char in scheme)
                and 0 < colon < len(userinfo) - 1 else match.group(0))

    return re.sub(r"://([^\s/@]+)@", hide_userinfo, value)


def redaction_prefix(value: str, limit: int) -> str:
    """Bound a displayed prefix without exposing URL userinfo cut before its @."""
    prefix = value[:limit]
    if len(value) <= limit:
        return prefix
    marker = prefix.rfind("://")
    if marker >= 0:
        tail = prefix[marker + 3:]
        if ":" in tail and not re.search(r"[\s/@]", tail):
            return prefix[:marker + 3] + "[REDACTED]"
    return prefix


def _cleanup_call(action) -> bool:
    try:
        action()
        return True
    except BaseException:
        return False


def _stop_tree(process: subprocess.Popen, job) -> tuple[bool, bool]:
    stopped = True
    if sys.platform == "linux" and os.name != "nt" and job is not None:
        try:
            contained = job.finish(process)
            return contained, contained
        except BaseException:
            # Failed supervision must never be reported as contained.
            _stop_tree(process, None)
            return False, False
    if os.name == "nt":
        terminated = _cleanup_call(job.terminate)
        stopped = terminated and stopped
        if terminated:
            stopped = _cleanup_call(job.wait_empty) and stopped
        stopped = _cleanup_call(job.close) and stopped
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except BaseException:
            stopped = False
    try:
        running = process.poll() is None
    except BaseException:
        stopped, running = False, True
    if running:
        stopped = _cleanup_call(process.kill) and stopped
    stopped = _cleanup_call(lambda: process.wait(timeout=10)) and stopped
    return stopped, stopped and os.name == "nt"


def _output_size(output, error) -> int:
    return os.fstat(output.fileno()).st_size + os.fstat(error.fileno()).st_size


def _wait(process, output, error, deadline: float, limit: int = MAX_OUTPUT) -> tuple[bool, bool]:
    while process.poll() is None:
        timed_out = time.monotonic() >= deadline
        truncated = _output_size(output, error) > limit
        if timed_out or truncated:
            return timed_out, truncated
        time.sleep(0.05)
    return False, _output_size(output, error) > limit


def _feed_stdin(handle, source) -> None:
    try:
        handle.write(b"1")
        handle.flush()
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            handle.write(chunk)
            handle.flush()
    except (BrokenPipeError, OSError, ValueError):
        pass
    finally:
        try:
            handle.close()
        except OSError:
            pass


def _abort_unassigned(process, job) -> bool:
    stopped = True
    if process is not None:
        stopped = _cleanup_call(process.kill) and stopped
        stopped = _cleanup_call(lambda: process.wait(timeout=10)) and stopped
    return _cleanup_call(job.close) and stopped


def _start_windows(command: list[str], cwd: Path, env: dict, source, output, error):
    from .windows_job import WindowsJob

    job = WindowsJob()
    bootstrap = Path(__file__).with_name("process_bootstrap.py")
    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, str(bootstrap), *command], cwd=cwd, env=env,
            stdin=subprocess.PIPE, stdout=output, stderr=error, shell=False,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
        )
        job.assign(process.pid)
    except BaseException as exc:
        if not _abort_unassigned(process, job):
            raise StoryLoopError(
                "Unable to close blocked process bootstrap",
                code="UNSAFE_CONTAINMENT",
            ) from exc
        raise
    feeder = None
    try:
        feeder = threading.Thread(
            target=_feed_stdin, args=(process.stdin, source), daemon=True,
        )
        feeder.start()
    except BaseException as exc:
        stopped, contained = _cleanup_process(process, job, feeder)
        if not stopped or not contained:
            raise StoryLoopError(
                "Unable to close external process tree",
                code="UNSAFE_CONTAINMENT",
            ) from exc
        raise
    return process, job, feeder


def _join_feeder(feeder, stopped: bool, contained: bool) -> tuple[bool, bool]:
    if not feeder:
        return stopped, contained
    joined = _cleanup_call(lambda: feeder.join(timeout=10))
    try:
        alive = feeder.is_alive()
    except BaseException:
        alive = True
    stopped = stopped and joined and not alive
    return stopped, contained and stopped


def _cleanup_process(process, job, feeder) -> tuple[bool, bool]:
    try:
        stopped, contained = _stop_tree(process, job)
        return _join_feeder(feeder, stopped, contained)
    except BaseException:
        return False, False


def _start_process(command: list[str], cwd: Path, env: dict, source, output, error):
    if os.name == "nt":
        return _start_windows(command, cwd, env, source, output, error)
    if sys.platform == "linux":
        from .linux_job import start_linux
        return start_linux(command, cwd, env, source, output, error)
    process = subprocess.Popen(command, cwd=cwd, env=env, stdin=source, stdout=output,
                               stderr=error, shell=False, start_new_session=True)
    return process, None, None


def _environment(allowlist: Sequence[str] | None,
                 source: Mapping[str, str] | None) -> dict[str, str]:
    names = SAFE_ENV if allowlist is None else {name.upper() for name in allowlist}
    values = os.environ if source is None else source
    environment = {
        key: value for key, value in values.items()
        if key.upper() in names
        and not any(marker in key.upper() for marker in SENSITIVE_ENV_MARKERS)
    }
    environment.update({"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"})
    return environment


def run_process(command: list[str], cwd: Path, timeout: int | float,
                input_text: str | None = None,
                environment_allowlist: Sequence[str] | None = None,
                source_environment: Mapping[str, str] | None = None,
                absolute_deadline: float | None = None,
                max_output: int | None = None) -> dict:
    """Execute argv, clean its tree, and report whether containment was secure.

    `max_output` raises the combined stdout/stderr cap for callers that stream events.
    """
    limit = int(max_output) if max_output else MAX_OUTPUT
    env = _environment(environment_allowlist, source_environment)
    result = {"returncode": None, "stdout": "", "stderr": "", "timed_out": False,
              "truncated": False, "error": "", "contained": False}
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as output, \
            tempfile.TemporaryFile() as error:
        source.write((input_text or "").encode("utf-8"))
        source.seek(0)
        try:
            process, job, feeder = _start_process(command, cwd, env, source, output, error)
        except (OSError, ValueError):
            result["error"] = "Unable to start configured executable"
            return result
        try:
            result["timed_out"], result["truncated"] = _wait(
                process, output, error,
                min(time.monotonic() + timeout, absolute_deadline)
                if absolute_deadline is not None else time.monotonic() + timeout,
                limit,
            )
        except BaseException as exc:
            stopped, contained = _cleanup_process(process, job, feeder)

            if not stopped or not contained:
                raise StoryLoopError(
                    "Unable to close external process tree",
                    code="UNSAFE_CONTAINMENT",
                ) from exc
            raise
        stopped, contained = _cleanup_process(process, job, feeder)
        result["truncated"] = result["truncated"] or _output_size(output, error) > limit
        result["contained"] = contained
        result["returncode"] = process.returncode
        if not stopped:
            result["error"] = "Unable to close external process tree"
        for key, handle in (("stdout", output), ("stderr", error)):
            handle.seek(0)
            result[key] = handle.read(limit).decode("utf-8", errors="replace")
    return result
