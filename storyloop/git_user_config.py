"""Read only inert Git preferences for explicit, isolated engine Git options."""

from __future__ import annotations

import json
from pathlib import Path
import re

from .errors import StoryLoopError


_SECTION = re.compile(r'^\[\s*([A-Za-z][A-Za-z0-9-]*)(?:\s+"[^"]*")?\s*\]$')
_ALLOWED = {"user.name", "user.email", "core.autocrlf", "core.safecrlf", "safe.directory"}
_SINGLE = ("user.name", "user.email", "core.autocrlf", "core.safecrlf")


def _config_values(path: Path) -> dict[str, list[str]]:
    """Extract inert allowlisted values; never retain credential/helper fields."""
    if not path.is_file():
        return {}
    if path.stat().st_size > 1_000_000:
        raise StoryLoopError("Git configuration is too large to read safely")
    values: dict[str, list[str]] = {}
    section = ""
    with path.open(encoding="utf-8-sig") as stream:
        for raw in stream:
            line = raw.strip()
            if not line or line.startswith(("#", ";")):
                continue
            match = _SECTION.fullmatch(line)
            if match:
                section = match.group(1).casefold()
                continue
            if "=" not in line:
                continue
            name, raw_value = line.split("=", 1)
            key = f"{section}.{name.strip().casefold()}"
            if key not in _ALLOWED:
                continue
            value = raw_value.strip()
            if value.startswith('"'):
                try:
                    value = json.loads(value)
                except ValueError as exc:
                    raise StoryLoopError(f"Invalid Git configuration value for {key}") from exc
            if not isinstance(value, str) or any(char in value for char in "\0\r\n"):
                raise StoryLoopError(f"Invalid Git configuration value for {key}")
            values.setdefault(key, []).append(value)
    return values


def _global_paths(environment: dict[str, str]) -> list[Path]:
    configured = environment.get("GIT_CONFIG_GLOBAL")
    if configured:
        return [Path(configured)]
    home = environment.get("HOME") or environment.get("USERPROFILE")
    if not home:
        return []
    xdg = environment.get("XDG_CONFIG_HOME")
    config = Path(xdg) if xdg else Path(home) / ".config"
    return [config / "git/config", Path(home) / ".gitconfig"]


def _trusted_directory(project: Path, raw: str) -> bool:
    if raw == "*":
        return True
    if raw.endswith("/*"):
        return project.resolve().is_relative_to(Path(raw[:-2]).expanduser().resolve())
    return project.resolve() == Path(raw).expanduser().resolve()


def explicit_global_options(project: Path, environment: dict[str, str]) -> list[str]:
    """Preserve harmless global identity/line-ending/trust choices as explicit -c values."""
    global_values: dict[str, list[str]] = {}
    for path in _global_paths(environment):
        for key, items in _config_values(path).items():
            global_values.setdefault(key, []).extend(items)
    local_values = _config_values(project / ".git/config")
    options = []
    for key in _SINGLE:
        if key in local_values or key not in global_values:
            continue
        value = global_values[key][-1]
        if key == "core.autocrlf" and value.casefold() not in {"true", "false", "input"}:
            raise StoryLoopError("Invalid global core.autocrlf")
        if key == "core.safecrlf" and value.casefold() not in {"true", "false", "warn"}:
            raise StoryLoopError("Invalid global core.safecrlf")
        options.extend(("-c", f"{key}={value}"))
    if any(_trusted_directory(project, item) for item in global_values.get("safe.directory", [])):
        options.extend(("-c", f"safe.directory={project.resolve()}"))
    return options
