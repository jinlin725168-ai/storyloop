"""Capability handshake, response validation and cache format without role policy."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys

from .errors import StoryLoopError, require
from .path_security import repository_relative
from .processutil import redact, run_process
from .storage import load_json

PROTOCOL = "storyloop.adapter-capabilities.v1"
ROLES = ("product", "acceptance", "implement", "review", "survey", "characterize", "design")
ROUTING = ("light", "standard", "heavy")


def normalize_vendor(value: object) -> str | None:
    return value.strip().casefold() if isinstance(value, str) and value.strip() else None


def expand_agent_token(value: str, project: Path) -> str:
    """Use the running engine's interpreter for both handshake and transport."""
    return {"{python}": sys.executable, "{project}": str(project)}.get(value, value)


def probe(project: Path, adapter_name: str, adapter: dict) -> dict:
    """Ask one adapter for its capability record; unsupported handshakes stay unknown."""
    kind = adapter.get("kind")
    if kind == "manual":
        return {"known": True, "tiers": []}
    if kind == "codex" and not isinstance(adapter.get("models"), dict):
        return _unknown("Codex model mapping is missing")
    if kind == "codex":
        return _codex_capabilities(adapter)
    if kind != "command":
        return _unknown("Adapter kind is unsupported")
    command = adapter.get("agent_command")
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        return _unknown("Agent command is invalid")
    argv = _argv(project, command)
    if not argv:
        return _unknown("Agent executable is unavailable")
    result = run_process(argv, Path(project), 30, input_text=json.dumps({"protocol": PROTOCOL}))
    if result.get("returncode") != 0 or result.get("timed_out") or result.get("error"):
        return _unknown(_process_failure(result))
    return _response(result.get("stdout"))


def _unknown(reason: str) -> dict:
    return {"known": False, "tiers": [], "reason": reason[:600]}


def _process_failure(result: dict) -> str:
    parts = [f"exit {result['returncode']}"] if result.get("returncode") not in (None, 0) else []
    parts += [name for name in ("timed_out", "truncated", "error") if result.get(name)]
    stderr = redact(str(result.get("stderr") or ""))
    tail = next((line.strip() for line in reversed(stderr.splitlines()) if line.strip()), "")
    return "; ".join(parts + ([tail[:500]] if tail else [])) or "Handshake process failed"


def _require_tier(role: str, setting: dict, adapter: dict, capability: dict) -> None:
    if adapter.get("kind") == "manual" or not capability["known"]:
        return
    if setting["tier"] not in capability["tiers"]:
        raise StoryLoopError("ADAPTER_PROFILE_UNSUPPORTED", code="ADAPTER_PROFILE_UNSUPPORTED",
                             details={"role": role, "tier": setting["tier"], "adapter": setting["adapter"]})


def _response(raw: object) -> dict:
    try:
        value = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        value = None
    if not _complete_capability(value):
        return _unknown("Capability response is invalid or lacks a vendor")
    tiers = value["tiers"]
    required_flags = ("subagents", "path_guard", "fast_checks")
    return {"known": True, **lane_metadata(value), "tiers": sorted(set(tiers)),
            "vendor": normalize_vendor(value["vendor"]),
            **{key: value[key] for key in (*required_flags, "modes")}}


def _codex_capabilities(adapter: dict) -> dict:
    models = adapter.get("models") or {}
    tiers = [tier for tier in ("strong", "standard") if isinstance(models.get(tier), str) and models[tier]]
    return {"known": True, "tiers": tiers, "vendor": "openai", "subagents": False,
            "path_guard": False, "fast_checks": False, "modes": ["*"]}


def _load_cache(project: Path, adapters: dict) -> dict | None:
    try:
        value = load_json(project / ".storyloop/state/adapters.json")
    except StoryLoopError:
        return None
    required = {"version", "config_sha256", "argv_sha256", "adapters"}
    if set(value) != required or value.get("version") != 1 or not isinstance(value.get("adapters"), dict):
        return None
    if not all(isinstance(value.get(key), str) and len(value[key]) == 64 for key in ("config_sha256", "argv_sha256")):
        return None
    if set(value["adapters"]) != set(adapters):
        return None
    if not all(_valid_cached_capability(adapters[name], item)
               for name, item in value["adapters"].items()):
        return None
    return {**value, "adapters": {
        name: ({**item, "vendor": normalize_vendor(item["vendor"])} if "vendor" in item else item)
        for name, item in value["adapters"].items()}}


def _valid_cached_capability(adapter: dict, value: object) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("known"), bool):
        return False
    if value["known"]:
        return adapter.get("kind") == "manual" and value == {"known": True, "tiers": []} or _complete_capability(value)
    return value == {"known": False, "tiers": []}


def _complete_capability(value: object) -> bool:
    if not isinstance(value, dict) or value.get("known", True) is not True:
        return False
    if normalize_vendor(value.get("vendor")) is None:
        return False
    tiers, modes = value.get("tiers"), value.get("modes")
    return (lane_metadata(value) is not None and isinstance(tiers, list) and all(isinstance(item, str) and item in {"strong", "standard"} for item in tiers)
            and all(isinstance(value.get(key), bool) for key in ("subagents", "path_guard", "fast_checks"))
            and isinstance(modes, list) and all(isinstance(item, str) and item for item in modes))


def _argv(project: Path, command: list[str]) -> list[str] | None:
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        return None
    argv = [expand_agent_token(item, project) for item in command]
    executable = argv[0] if Path(argv[0]).is_absolute() else shutil.which(argv[0])
    return [str(executable), *argv[1:]] if executable else None


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _argv_digest(project: Path, adapters: dict) -> str:
    values = {name: _argv(project, value.get("agent_command", [])) if value.get("kind") == "command" else value.get("models")
              for name, value in adapters.items()}
    return _digest(values)


def lane_metadata(value: dict) -> dict | None:
    """Validate optional capability metadata without accepting executable/path syntax."""
    names, prefixes = value.get('lanes', []), value.get('lane_prefixes', {})
    if (not isinstance(names, list) or len(names) > 32 or
            not all(isinstance(name, str) and name and len(name) <= 80 for name in names)
            or not isinstance(prefixes, dict) or set(prefixes) - set(names)):
        return None
    try:
        for prefix in prefixes.values():
            require(isinstance(prefix, str) and not any(c in prefix for c in '*?[];|&$()`\n\r'), 'Unsafe lane prefix')
            require(repository_relative(prefix) == prefix.rstrip('/'), 'Invalid lane prefix')
    except StoryLoopError:
        return None
    return {key: value[key] for key in ('lanes', 'lane_prefixes') if key in value}
