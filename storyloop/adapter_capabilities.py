"""Capability handshake and cache for named project-agent adapters."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

from .baseline import load_project_config
from .models import StoryLoopError, require
from .processutil import run_process
from .role_profiles import enabled, resolve_adapters, role_settings
from .storage import load_json, write_json

PROTOCOL = "storyloop.adapter-capabilities.v1"
ROLES = ("product", "acceptance", "implement", "review", "survey", "characterize", "design")
ROUTING = ("light", "standard", "heavy")


def probe(project: Path, adapter_name: str, adapter: dict) -> dict:
    """Ask one adapter for its capability record; unsupported handshakes stay unknown."""
    kind = adapter.get("kind")
    if kind == "manual":
        return {"known": True, "tiers": []}
    if kind == "codex" and not isinstance(adapter.get("models"), dict):
        return {"known": False, "tiers": []}
    if kind == "codex":
        return _codex_capabilities(adapter)
    if kind != "command":
        return {"known": False, "tiers": []}
    command = adapter.get("agent_command")
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        return {"known": False, "tiers": []}
    argv = _argv(project, command)
    if not argv:
        return {"known": False, "tiers": []}
    result = run_process(argv, Path(project), 30, input_text=json.dumps({"protocol": PROTOCOL}))
    if result.get("returncode") != 0 or result.get("timed_out") or result.get("error"):
        return {"known": False, "tiers": []}
    return _response(result.get("stdout"))


def check(project: Path) -> dict:
    """Probe configured adapters, reject known missing mappings, and replace the cache."""
    project, config = Path(project).resolve(), load_project_config(project)
    if not enabled(config):
        return {"status": "ADAPTERS_UNCONFIGURED", "adapters": {}}
    adapters = resolve_adapters(config)
    results = {name: probe(project, name, adapter) for name, adapter in adapters.items()}
    _require_mappings(config, adapters, results)
    record = {"version": 1, "config_sha256": _digest(config), "argv_sha256": _argv_digest(project, adapters),
              "adapters": results}
    write_json(project / ".storyloop/state/adapters.json", record, max_bytes=512_000)
    return {"status": "ADAPTERS_CHECKED", **record}


def ensure_checked(project: Path) -> dict | None:
    project, config = Path(project).resolve(), load_project_config(project)
    if not enabled(config):
        return None
    adapters = resolve_adapters(config)
    cached = _load_cache(project, adapters)
    if cached and cached["config_sha256"] == _digest(config) and cached["argv_sha256"] == _argv_digest(project, adapters):
        _require_mappings(config, adapters, cached["adapters"])
        return {"status": "ADAPTERS_CACHED", **cached}
    return check(project)


def _require_mappings(config: dict, adapters: dict, results: dict) -> None:
    for role in ROLES:
        for routing in ROUTING:
            setting = role_settings(config, role, routing)
            adapter, capability = adapters[setting["adapter"]], results[setting["adapter"]]
            if adapter.get("kind") == "manual" or not capability["known"]:
                continue
            if setting["tier"] not in capability["tiers"]:
                raise StoryLoopError("ADAPTER_PROFILE_UNSUPPORTED", code="ADAPTER_PROFILE_UNSUPPORTED",
                                     details={"role": role, "tier": setting["tier"], "adapter": setting["adapter"]})


def _response(raw: object) -> dict:
    try:
        value = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        value = None
    if not _complete_capability(value):
        return {"known": False, "tiers": []}
    tiers = value["tiers"]
    required_flags = ("subagents", "path_guard", "fast_checks")
    return {"known": True, "tiers": sorted(set(tiers)), "vendor": value["vendor"],
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
    return value if all(_valid_cached_capability(adapters[name], item)
                        for name, item in value["adapters"].items()) else None


def _valid_cached_capability(adapter: dict, value: object) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("known"), bool):
        return False
    if value["known"]:
        return adapter.get("kind") == "manual" and value == {"known": True, "tiers": []} or _complete_capability(value)
    return value == {"known": False, "tiers": []}


def _complete_capability(value: object) -> bool:
    if not isinstance(value, dict) or value.get("known", True) is not True:
        return False
    if not isinstance(value.get("vendor"), str) or not value["vendor"]:
        return False
    tiers, modes = value.get("tiers"), value.get("modes")
    return (isinstance(tiers, list) and all(item in {"strong", "standard"} for item in tiers)
            and all(isinstance(value.get(key), bool) for key in ("subagents", "path_guard", "fast_checks"))
            and isinstance(modes, list) and all(isinstance(item, str) and item for item in modes))


def _argv(project: Path, command: list[str]) -> list[str] | None:
    argv = [item.replace("{python}", shutil.which("python") or "python").replace("{project}", str(project))
            for item in command]
    executable = argv[0] if Path(argv[0]).is_absolute() else shutil.which(argv[0])
    return [str(executable), *argv[1:]] if executable else None


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _argv_digest(project: Path, adapters: dict) -> str:
    values = {name: _argv(project, value.get("agent_command", [])) if value.get("kind") == "command" else value.get("models")
              for name, value in adapters.items()}
    return _digest(values)
