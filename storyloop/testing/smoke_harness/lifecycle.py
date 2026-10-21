"""Explicit stages, strict results, and unconditional cleanup of owned resources."""
from dataclasses import dataclass, field, replace
from pathlib import Path

from ...models import require
from ...onboarding_manifest import validate_hooks
from .cases import validate_cases
from .processes import Service
from .results import Result
from .scratch import Scratch


@dataclass(frozen=True)
class Context:
    """Protected invocation inputs; resources belong to this invocation only."""
    project: Path
    cases: list[str]
    executor: dict
    scratch: Scratch | None = None
    services: list = field(default_factory=list)

    def start_service(self, argv: list[str], cwd: Path) -> Service:
        """Start and register a service for run_hooks' unconditional process-tree cleanup."""
        service = Service(argv, cwd)
        self.services.append(service)
        return service


def run_hooks(context: Context, **hooks) -> Result:
    """Validate before build, run selected cases, and make cleanup failures dominate RED."""
    result = Result()
    active = None
    try:
        validate_hooks(context.executor)
        cases = validate_cases(context.project, context.cases)
        required = {name for name, item in context.executor["hooks"].items()
                    if item["status"] == "implemented"}
        require(all(callable(hooks.get(name)) for name in required), "Required hook is unimplemented")
        active = replace(context, cases=cases, scratch=Scratch(context.project), services=[])
        _prepare(active)
        for stage in ("build", "start", "ready"):
            if stage in required:
                require(hooks[stage](active) is None, "Infrastructure hook must succeed or raise")
        for case in cases:
            item = hooks["run_case"](active, case)
            require(isinstance(item, Result), "Unrecognized case runner evidence")
            result += item if item.executed else item + Result(errors=1)
    except BaseException:
        result += Result(errors=1)
    finally:
        if active is not None:
            result += _cleanup(active, hooks)
    return result


def _prepare(context: Context) -> None:
    context.scratch.sync(context.project, context.executor["source_files"])
    for cache in context.executor["dependency_caches"]:
        require(isinstance(cache, dict) and set(cache) == {"name", "root", "files"}, "Invalid cache declaration")
        root = Path(cache["root"])
        if not root.is_absolute():
            root = context.project / root
        context.scratch.cache(cache["name"], root, cache["files"])


def _cleanup(context: Context, hooks: dict) -> Result:
    errors = 0
    if context.executor["hooks"]["cleanup"]["status"] == "implemented":
        try:
            require(hooks["cleanup"](context) is None, "Cleanup hook must succeed or raise")
        except BaseException:
            errors += 1
    for service in reversed(context.services):
        try:
            service.close()
        except BaseException:
            errors += 1
    try:
        context.scratch.close()
    except BaseException:
        errors += 1
    return Result(errors=errors)
