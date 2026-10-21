"""Absolute-path, isolated entry point resolved only by the engine."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys

ENGINE_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ENGINE_ROOT))
from storyloop import __version__
from storyloop.models import require
from storyloop.path_security import safe_target
from storyloop.onboarding_manifest import load_manifest, validate_hooks
from storyloop.testing.smoke_harness.v1 import Context, Result, validate_cases
from storyloop.testing.smoke_harness.results import PREFIX


def execute(project: Path, arguments: list[str]) -> Result:
    require(bool(arguments), "Protected executor required")
    require(Path(sys.modules["storyloop"].__file__).resolve().parent == ENGINE_ROOT / "storyloop",
            "Untrusted helper installation")
    manifest = load_manifest(project)
    require(manifest.get("engine_version") == __version__ and manifest.get("helper_api") == 1,
            "Unsupported helper version; install the pinned engine offline")
    executor = manifest.get("executors", {}).get(arguments[0])
    require(isinstance(executor, dict), "Executor is not declared")
    validate_hooks(executor)
    cases = validate_cases(project, arguments[1:], manifest.get("case_roots", []))
    path = safe_target(project, arguments[0], "protected executor")
    spec = importlib.util.spec_from_file_location("storyloop_protected_executor", path)
    module = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        spec.loader.exec_module(module)
        result = module.run(Context(project, cases, executor))
    require(isinstance(result, Result), "Executor returned unrecognized evidence")
    return result


def main() -> int:
    try:
        result = execute(Path.cwd().resolve(), sys.argv[1:])
    except BaseException:
        result = Result(errors=1)
    print(PREFIX + json.dumps(result.document(), sort_keys=True))
    return result.code


if __name__ == "__main__":
    sys.exit(main())
