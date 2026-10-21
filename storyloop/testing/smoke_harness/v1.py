"""Supported helper API v1; onboarding.json additionally pins the exact engine version.

validate_cases(project: Path, cases: list[str], roots: list[str] | None = None) -> list[str]
python_cases(project: Path, cases: list[str], timeout: float = 60, *, cwd: Path | None = None) -> Result
Result(executed=0, failed=0, errors=0, skipped=0): immutable counts; .code is strict 0/1/2.
Context(project: Path, cases: list[str], executor: dict): protected invocation inputs.
  .start_service(argv: list[str], cwd: Path) -> Service registers automatic tree cleanup.
run_hooks(context: Context, **hooks: Callable) -> Result runs phases and finally cleanup.
Scratch(project: Path, base: Path | None = None) owns .root/.mirror and a lease token.
  .sync(project: Path, files: list[str]) -> Path copies explicit safe files by content.
  .cache(name: str, root: Path, files: list[str]) -> Path copies dependency archives only.
  .close() -> None verifies ownership and links before removing this lease.
run_command(argv: list[str], cwd: Path, timeout: float = 60) -> dict executes native argv.
Service(argv: list[str], cwd: Path) starts a contained tree; .close() reclaims it.
wait_http(url: str, expected_status: int = 200, timeout: float = 5) -> None is a GET probe.
free_port() -> int is advisory; a binding race is a start error.

Project executors export run(context: Context) -> Result. They must not put candidate
paths in the helper interpreter's sys.path; business code must execute in a separate subprocess, never be imported in the judge.
python_cases loads only protected unittest files without adding candidate paths to sys.path.
Its timeout is cooperative; the manifest supplies the hard process deadline.
Infrastructure hooks return None on success or raise; run_case returns measured Result.
Only unittest Python case files are built in. Other runners need an explicit protected
adapter that separates actual executions, assertion failures and environment errors;
unknown outcomes must raise HarnessError, never synthesize PASS or a RED exit code.
"""
from ...models import StoryLoopError as HarnessError
from .cases import validate_cases
from .health import free_port, wait_http
from .lifecycle import Context, run_hooks
from .processes import Service, run_command
from .python_runner import python_cases
from .results import Result
from .scratch import Scratch
