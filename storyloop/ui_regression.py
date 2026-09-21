"""Optional post-implementation UI regression assets produced by an external generator.

The generator is any command configured in `.storyloop/config.json` under `ui_regression`:

    "ui_regression": {"command": ["{python}", "acceptance/tools/ui_regression.py"], "timeout_seconds": 3600}

It receives a request on stdin (`storyloop.ui-regression.v1`: change, capability, suite root,
stories) and prints `{files: [{path, content}]}`. How it works (starting the app, driving the
Playwright planner/generator/healer, or anything else) is the project's business. The engine
only places the files under `acceptance/ui/<capability>/` and commits them with the report.

These files are regression assets and living UI documentation, never acceptance evidence: they
are written against the finished implementation and pass by construction on first run.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

from .models import StoryLoopError, identifier, require
from .path_security import safe_target
from .processutil import run_process

SUITE_ROOT = "acceptance/ui"
MAX_FILES = 200
MAX_FILE_BYTES = 512_000


def generate_ui_assets(project: Path, change: dict, capability: str, config: dict) -> dict:
    """Run the configured generator (if any) and return its raw payload plus a status."""
    settings = config.get("ui_regression")
    if not settings:
        return {"status": "SKIPPED", "reason": "ui_regression not configured"}
    require(isinstance(settings, dict) and isinstance(settings.get("command"), list) and settings["command"],
            "ui_regression.command must be an argv list")
    argv = [_expand(item, project) for item in settings["command"]]
    executable = argv[0] if Path(argv[0]).is_absolute() else shutil.which(argv[0])
    require(bool(executable), "ui_regression command is unavailable")
    argv[0] = str(executable)
    request = {"protocol": "storyloop.ui-regression.v1", "role": "ui_regression", "project": str(project),
               "payload": {"change_id": change["change_id"], "capability": capability,
                           "suite_root": f"{SUITE_ROOT}/{capability}",
                           "goal": change["product"]["goal"], "stories": change["product"]["stories"]}}
    result = run_process(argv, project, int(settings.get("timeout_seconds", 3600)),
                         input_text=json.dumps(request, ensure_ascii=False), max_output=16 * 1024 * 1024)
    if result.get("contained") is not True or result.get("timed_out") or result.get("truncated") \
            or result.get("error") or result.get("returncode") != 0:
        tail = str(result.get("stderr") or "").strip().splitlines()[-3:]
        raise StoryLoopError("UI regression generator failed: " + " / ".join(tail)[:600])
    try:
        payload = json.loads(result.get("stdout", ""))
    except ValueError:
        raise StoryLoopError("UI regression generator must print one JSON object") from None
    require(isinstance(payload, dict) and isinstance(payload.get("files"), list),
            "UI regression generator must return {files:[{path,content}]}")
    return {"status": "GENERATED", "payload": payload}


def install_ui_assets(project: Path, capability: str, payload: dict) -> list[str]:
    """Write generated files under acceptance/ui/<capability>/ and return their repo paths."""
    project = Path(project).resolve()
    capability = identifier(capability, "capability")
    files = payload.get("files") if isinstance(payload, dict) else None
    require(isinstance(files, list) and len(files) <= MAX_FILES, "UI regression files must be a bounded list")
    written: list[str] = []
    for item in files:
        require(isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("content"), str),
                "Each UI regression file needs a string path and string content")
        require(len(item["content"].encode("utf-8")) <= MAX_FILE_BYTES, "UI regression file is too large")
        relative = f"{SUITE_ROOT}/{capability}/{item['path']}"
        target = safe_target(project, relative, "UI regression path")
        require(target.is_relative_to(project / SUITE_ROOT / capability), "UI regression path escapes its suite root")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item["content"], encoding="utf-8")
        written.append(target.relative_to(project).as_posix())
    return written


def _expand(value: str, project: Path) -> str:
    return {"{python}": sys.executable, "{project}": str(project)}.get(value, value)
