"""Mutable role configuration and cross-vendor capability checks."""
from __future__ import annotations

from pathlib import Path

from .baseline import load_project_config
from .capability_protocol import (PROTOCOL, ROLES, ROUTING, probe, _argv_digest, _digest,  # noqa: F401  # 兼容再导出：保留旧模块导入路径
                                  _load_cache, _require_tier)
from .models import StoryLoopError, require
from .role_profiles import enabled, resolve_adapters, role_settings
from .storage import write_json


def check(project: Path) -> dict:
    """Probe configured adapters, reject known missing mappings, and replace the cache."""
    project, config = Path(project).resolve(), load_project_config(project)
    if not enabled(config):
        from .cross_vendor import require_distinct
        require_distinct(config, {})
        return {"status": "ADAPTERS_UNCONFIGURED", "adapters": {}}
    adapters = resolve_adapters(config)
    results = {name: probe(project, name, adapter) for name, adapter in adapters.items()}
    record = {"version": 1, "config_sha256": _digest(config), "argv_sha256": _argv_digest(project, adapters),
              "adapters": {name: {key: value for key, value in result.items() if key != "reason"}
                           for name, result in results.items()}}
    cache = project / ".storyloop/state/adapters.json"
    try:
        _require_mappings(config, adapters, results)
    except StoryLoopError as error:
        if error.code == "AGENT_UNAVAILABLE" and error.details.get("cross_vendor"):
            # Keep the observed unknown capability too: guarded role calls must not rewrite cache.
            write_json(cache, record, max_bytes=512_000)
        raise
    write_json(cache, record, max_bytes=512_000)
    return {"status": "ADAPTERS_CHECKED", **record, "adapters": results}


def ensure_checked(project: Path) -> dict | None:
    project, config = Path(project).resolve(), load_project_config(project)
    if not enabled(config):
        from .cross_vendor import require_distinct
        require_distinct(config, {})
        return None
    adapters = resolve_adapters(config)
    cached = _load_cache(project, adapters)
    if cached and cached["config_sha256"] == _digest(config) and cached["argv_sha256"] == _argv_digest(project, adapters):
        _require_mappings(config, adapters, cached["adapters"])
        return {"status": "ADAPTERS_CACHED", **cached}
    return check(project)


def require_base_review_vendors(project: Path, config: dict) -> None:
    """Prove selected base role vendors before invoking design or implementation."""
    record = ensure_checked(project)
    capabilities = (record or {}).get("adapters", {})
    for role in ("implement", "review"):
        adapter = role_settings(config, role, "heavy", base=True)["adapter"]
        if not capabilities.get(adapter, {}).get("vendor"):
            raise StoryLoopError(
                f"Base {role} adapter {adapter} vendor handshake is unknown; "
                "repair adapter configuration and run storyloop adapters check <project> "
                "before starting this agent run", code="AGENT_UNAVAILABLE")


def _require_mappings(config: dict, adapters: dict, results: dict) -> None:
    from .cross_vendor import require_distinct
    for role in ROLES:
        for routing in ROUTING:
            setting = role_settings(config, role, routing)
            name = setting["adapter"]
            _require_tier(role, setting, adapters[name], results[name])
    require_distinct(config, results)


def require_role_target(project: Path, config: dict, role: str, setting: dict) -> None:
    """Check the actual fallback target using validated cached observations, without writing."""
    adapters = resolve_adapters(config)
    cached = _load_cache(project, adapters)
    require(cached is not None and cached["config_sha256"] == _digest(config)
            and cached["argv_sha256"] == _argv_digest(project, adapters),
            "Adapter capabilities changed; run adapters check")
    name = setting["adapter"]
    _require_tier(role, setting, adapters[name], cached["adapters"][name])


def ensure_for_run(project: Path) -> dict | None:
    """Retry cached cross-vendor unknowns once at run entry, outside agent guards."""
    from .cross_vendor import policy
    config = load_project_config(project)
    adapters = resolve_adapters(config)
    cached = _load_cache(project, adapters)
    if (cached and cached["config_sha256"] == _digest(config)
            and cached["argv_sha256"] == _argv_digest(project, adapters) and policy(config)['enabled']):
        names = [role_settings(config, role, None)['adapter'] for role in ('implement', 'review')]
        if any(not cached['adapters'][name].get('vendor') for name in names):
            return check(project)
    return ensure_checked(project)
