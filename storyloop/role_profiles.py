"""Resolve role-specific adapter requests without changing the legacy protocol."""

from __future__ import annotations

from pathlib import Path

from .models import require
from .project_boundaries import role_path_boundary
from .skills import ROLE_SKILLS, instruction_skill_snapshot

TIERS = {"strong", "standard"}
ROUTING = {"light", "standard", "heavy"}
DECISION_ROLES = {"product", "acceptance", "design", "review"}
WRITERS = {"implement", "characterize"}


def enabled(config: dict) -> bool:
    return bool(config.get("adapters") or config.get("roles"))


def resolve_adapters(config: dict) -> dict[str, dict]:
    """Return named adapters, representing the old pair as ``default``."""
    configured = config.get("adapters")
    if configured is None:
        kind = config.get("adapter")
        return {"default": {"kind": kind, "agent_command": list(config.get("agent_command") or [])}}
    require(isinstance(configured, dict) and configured, "adapters must be a nonempty object")
    result = {}
    for name, adapter in configured.items():
        require(isinstance(name, str) and name and isinstance(adapter, dict), "Invalid adapter")
        kind = adapter.get("kind")
        require(kind in {"manual", "command", "codex"}, "Invalid adapter kind")
        result[name] = dict(adapter)
    return result


def role_settings(config: dict, role: str, routing_tier: str | None) -> dict:
    """Resolve the adapter and requested tier for one engine role."""
    routing = routing_tier or "standard"
    require(routing in ROUTING, "Invalid routing tier")
    roles = config.get("roles") or {}
    require(isinstance(roles, dict), "roles must be an object")
    override = roles.get(role) or {}
    require(isinstance(override, dict), "Invalid role profile")
    adapters = resolve_adapters(config)
    adapter = override.get("adapter", roles.get("default_adapter", "default"))
    require(isinstance(adapter, str) and adapter in adapters, "Role references an unknown adapter")
    tier = _tier(role, routing, override.get("tier"))
    if role in DECISION_ROLES and tier == "standard" and not override.get("reason"):
        require(False, f"roles.{role} lowering requires a reason")
    result = {"adapter": adapter, "tier": tier}
    if isinstance(override.get("reason"), str) and override["reason"].strip():
        result["reason"] = override["reason"].strip()
    if "fast_checks" in override:
        result["fast_checks"] = _commands(override["fast_checks"])
    return result


def _tier(role: str, routing: str, configured: object) -> str:
    default = "standard" if role in {"implement", "characterize", "survey"} and routing == "light" else "strong"
    if role in {"characterize", "survey"}:
        default = "standard"
    value = configured.get(routing) if isinstance(configured, dict) else configured
    if isinstance(configured, dict):
        require(set(configured) <= ROUTING and all(item in TIERS for item in configured.values()), "Invalid role tier")
    elif configured is not None:
        require(configured in TIERS, "Invalid role tier")
    return value or default


def build_profile(project: Path, config: dict, role: str, payload: dict, state: dict) -> dict | None:
    """Build the optional request extension; absence preserves legacy request bytes."""
    if not enabled(config):
        return None
    settings = role_settings(config, role, _routing_tier(payload, state))
    skills = [_skill(project, name) for name in ROLE_SKILLS.get(role, ())]
    profile = {"version": 1, "tier": settings["tier"], "routing_tier": _routing_tier(payload, state),
               "skills": skills, **role_path_boundary(project, role, payload),
               "fast_checks": settings.get("fast_checks", _fast_checks(config, role))}
    return profile


def _routing_tier(payload: dict, state: dict) -> str:
    value = state.get("routing_tier") or payload.get("routing_tier") or payload.get("tier") or "standard"
    return value if value in ROUTING else "standard"


def _skill(project: Path, name: str) -> dict:
    _, snapshot = instruction_skill_snapshot(project, name)
    return snapshot


def _fast_checks(config: dict, role: str) -> list[list[str]]:
    if role not in WRITERS:
        return []
    profile = config.get("profile") or {}
    return _commands(profile.get("check_commands") or [])


def _commands(value: object) -> list[list[str]]:
    require(isinstance(value, list) and all(isinstance(row, list) and row and
            all(isinstance(item, str) and item for item in row) for row in value), "Invalid fast_checks")
    return [list(row) for row in value]
