"""Absolute-path, isolated entry point resolved only by the engine."""
from __future__ import annotations

import contextlib
import importlib.util
import os
import json
from pathlib import Path
import sys
import traceback

ENGINE_ROOT = Path(__file__).resolve().parents[3]
PREFIX = "STORYLOOP_HARNESS_V1="


class ImportIsolationError(RuntimeError):
    """An editable finder cannot be proven safe for trusted helper imports."""


def _candidate_path(value: object, project: Path) -> bool:
    if not isinstance(value, (str, os.PathLike)):
        return False
    try:
        path = Path(value).resolve()
    except (OSError, ValueError):
        return False
    return path == project or project in path.parents


def _editable_module(finder: object) -> str:
    return str(getattr(finder, "__module__", type(finder).__module__))


def _editable_targets(module: object) -> list[object] | None:
    mapping = getattr(module, "MAPPING", None)
    namespaces = getattr(module, "NAMESPACES", None)
    if not isinstance(mapping, dict) and not isinstance(namespaces, dict):
        return None
    targets = list(mapping.values()) if isinstance(mapping, dict) else []
    for value in namespaces.values() if isinstance(namespaces, dict) else []:
        targets.extend(value if isinstance(value, (list, tuple)) else [value])
    return targets


def _isolate_imports(project: Path) -> None:
    """Remove candidate editable paths and finders before importing any engine helper."""
    loaded_candidate = [name for name, module in list(sys.modules.items())
                        if _candidate_path(getattr(module, "__file__", None), project)
                        or any(_candidate_path(path, project)
                               for path in (getattr(module, "__path__", None) or []))]
    sys.path[:] = [str(ENGINE_ROOT), *(entry for entry in sys.path
                                      if Path(entry).is_absolute()
                                      and not _candidate_path(entry, project)
                                      and Path(entry).resolve() not in project.parents)]
    removed, opaque = set(), False
    for finder in list(sys.meta_path):
        module_name = _editable_module(finder)
        if not module_name.startswith("__editable__"):
            continue
        targets = _editable_targets(sys.modules.get(module_name))
        unknown = targets is None or any(not isinstance(target, (str, os.PathLike))
                                         for target in targets)
        opaque = opaque or unknown
        if unknown or any(_candidate_path(target, project) for target in targets):
            sys.meta_path.remove(finder)
            removed.add(module_name)
    sys.path_hooks[:] = [hook for hook in sys.path_hooks
                         if _editable_module(hook) not in removed]
    sys.path_importer_cache.clear()
    for name in loaded_candidate:
        sys.modules.pop(name, None)
    if any(name == "storyloop" or name.startswith("storyloop.") for name in loaded_candidate):
        raise ImportIsolationError("Untrusted helper installation")
    if opaque:
        raise ImportIsolationError("Editable import finder cannot be isolated; remove the candidate editable install and retry")


def execute(project: Path, arguments: list[str]):
    _isolate_imports(project)
    from storyloop import __version__
    from storyloop.models import require
    from storyloop.path_security import safe_target
    from storyloop.onboarding_manifest import load_manifest, validate_hooks
    from storyloop.testing.smoke_harness.v1 import Context, Result, validate_cases
    from storyloop.testing.smoke_harness.protected_cases import protected_cases
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
    with protected_cases(project, cases), open(os.devnull, "w", encoding="utf-8") as sink, \
            contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        spec.loader.exec_module(module)
        result = module.run(Context(project, cases, executor))
    require(isinstance(result, Result), "Executor returned unrecognized evidence")
    return result


def main() -> int:
    project = Path.cwd().resolve()
    isolated = False
    try:
        _isolate_imports(project)
        isolated = True
        result = execute(project, sys.argv[1:])
        document = result.document()
    except BaseException as exc:
        if isolated:
            from storyloop.processutil import redact
            message = redact(f"{type(exc).__name__}: {exc}")[:2000]
            frames = []
            for frame in traceback.extract_tb(exc.__traceback__):
                try:
                    relative = Path(frame.filename).resolve().relative_to(project)
                except ValueError:
                    continue
                frames.append(f"{relative.as_posix()}:{frame.lineno} in {frame.name}")
            tail = redact("\n".join(frames))[-4000:]
        else:
            message = (f"{type(exc).__name__}: {exc}" if isinstance(exc, ImportIsolationError)
                       else f"{type(exc).__name__}: Import isolation failed")
            tail = ""
        document = {"executed": 0, "failed": 0, "errors": 1, "skipped": 0, "code": 2,
                    "diagnostics": ["launch", {"case_id": "launcher", "kind": "error",
                                             "message": message, "traceback_tail": tail}]}
    print(PREFIX + json.dumps(document, sort_keys=True))
    return document["code"]


if __name__ == "__main__":
    sys.exit(main())
