"""Native argv, whitelisted environment, and the engine's cross-platform tree containment."""
import os
from pathlib import Path
import shutil
import tempfile
import sys
import time

from ...models import StoryLoopError, require
from ...processutil import _cleanup_process, _environment, _start_process, run_process
from .python_runner import ENVIRONMENT

SHELLS = {"sh", "bash", "dash", "zsh", "ksh", "fish", "cmd", "powershell", "pwsh", "env"}


def native_argv(argv: list[str]) -> list[str]:
    """Reject shell strings, shell wrappers, and Windows script wrappers on every platform."""
    require(isinstance(argv, list) and 0 < len(argv) <= 100
            and all(isinstance(arg, str) and arg and "\0" not in arg for arg in argv),
            "Command must be a nonempty argv array")
    resolved = argv[0] if Path(argv[0]).is_absolute() else shutil.which(argv[0])
    require(Path(argv[0]).suffix.lower() not in {".cmd", ".bat", ".ps1"}
            and Path(argv[0]).stem.casefold() not in SHELLS, "Shell wrappers are not supported")
    require(bool(resolved) and Path(resolved).is_file(), "Native executable is unavailable")
    require(Path(resolved).suffix.lower() not in {".cmd", ".bat", ".ps1"}
            and Path(resolved).stem.casefold() not in SHELLS, "Native executable required")
    return [str(Path(resolved).absolute()), *argv[1:]]


def run_command(argv: list[str], cwd: Path, timeout: float = 60) -> dict:
    """Run an infrastructure command; any failure is an environment error, never RED."""
    require(type(timeout) in {int, float} and timeout > 0, "Positive timeout required")
    result = run_process(native_argv(argv), Path(cwd), timeout, environment_allowlist=ENVIRONMENT)
    require(result.get("contained") is True and result.get("returncode") == 0
            and not any(result.get(key) for key in ("error", "timed_out", "truncated")),
            "Infrastructure command failed; check the declared stage and offline dependencies")
    return {"returncode": 0, "contained": True}  # Do not leak arbitrary build/config output.


class Service:
    """Start a managed service tree; use Context.start_service for automatic finally cleanup."""
    def __init__(self, argv: list[str], cwd: Path):
        command = native_argv(argv)
        self._handles = [tempfile.TemporaryFile() for _ in range(3)]
        self._closed = False
        self.contained = False
        self._startup = tempfile.TemporaryDirectory(prefix="storyloop-service-")
        ready = Path(self._startup.name) / "ready"
        command = [str(Path(sys.executable).absolute()), "-I", "-B",
                   str(Path(__file__).with_name("service_bootstrap.py").resolve()), str(ready), *command]
        try:
            self.process, self._job, self._feeder = _start_process(
                command, Path(cwd), _environment(ENVIRONMENT, os.environ), *self._handles)
            self._wait_started(ready)
        except BaseException:
            if hasattr(self, "process"):
                _cleanup_process(self.process, self._job, self._feeder)
            self._startup.cleanup()
            for handle in self._handles:
                handle.close()
            raise StoryLoopError("Service failed to start") from None

    def _wait_started(self, ready: Path) -> None:
        deadline = time.monotonic() + 10
        while not ready.exists() and self.process.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        require(ready.exists(), "Service startup handshake failed")

    def close(self) -> None:
        """Reclaim the entire process tree, including children that outlive their parent."""
        if self._closed:
            return
        stopped, self.contained = _cleanup_process(self.process, self._job, self._feeder)
        for handle in self._handles:
            handle.close()
        self._closed = True
        self._startup.cleanup()
        require(stopped and self.contained, "Service cleanup failed; owned process tree may remain")

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()
