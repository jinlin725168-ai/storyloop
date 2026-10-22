"""Pure architecture analysis; declarations are consumed by tests only."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePath
import tomllib

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "storyloop"
DECLARATION = PACKAGE / "architecture.toml"
MODULES_MD = ROOT / "docs" / "modules.md"
ZONES = ("base", "mutable")
SPLIT_STAGES = ("B", "D")
EXEMPTION_STAGES = ("B", "C", "D")


@dataclass(frozen=True)
class ArchModule:
    key: str
    title: str
    rank: int


@dataclass(frozen=True)
class Entry:
    name: str
    module: str
    zone: str
    split: str | None


@dataclass(frozen=True)
class Exemption:
    source: str
    target: str
    until: str
    reason: str


@dataclass(frozen=True)
class Invariant:
    key: str
    title: str
    status: str
    tests: tuple[str, ...]
    stage: str | None


@dataclass(frozen=True)
class Declaration:
    architecture: dict[str, ArchModule]
    modules: dict[str, Entry]
    exemptions: tuple[Exemption, ...]
    cycle_members: frozenset[str]
    invariants: dict[str, Invariant]


def _table(value, key):
    if not isinstance(value, dict):
        raise ValueError(f"{key}: expected a table")
    return value


def _text(value, key):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key}: expected nonempty text")
    return value


def _choice(value, choices, key):
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{key}: invalid value {value!r}")
    return value


def _architecture(values):
    result = {}
    for key, raw in _table(values, "architecture").items():
        item = _table(raw, f"architecture.{key}")
        rank = item.get("rank")
        if type(rank) is not int or rank < 0:
            raise ValueError(f"architecture.{key}.rank: expected nonnegative integer")
        result[key] = ArchModule(key, _text(item.get("title"), f"architecture.{key}.title"), rank)
    return result


def _entries(values, architecture):
    result = {}
    for name, raw in _table(values, "modules").items():
        item = _table(raw, f"modules.{name}")
        module = _choice(item.get("module"), architecture, f"modules.{name}.module")
        zone = _choice(item.get("zone"), ZONES, f"modules.{name}.zone")
        split = item.get("split")
        if split is not None:
            _choice(split, SPLIT_STAGES, f"modules.{name}.split")
        result[name] = Entry(name, module, zone, split)
    return result


def _exemptions(values):
    if not isinstance(values, list):
        raise ValueError("exemptions: expected array of tables")
    result = []
    for index, raw in enumerate(values):
        key = f"exemptions.{index}"
        item = _table(raw, key)
        result.append(Exemption(
            _text(item.get("source"), f"{key}.source"), _text(item.get("target"), f"{key}.target"),
            _choice(item.get("until"), EXEMPTION_STAGES, f"{key}.until"),
            _text(item.get("reason"), f"{key}.reason"),
        ))
    return tuple(result)


def load_declaration(path: Path = DECLARATION) -> Declaration:
    """Read a versioned declaration, naming invalid fields in ValueError messages."""
    with path.open("rb") as stream:
        values = tomllib.load(stream)
    if type(values.get("version")) is not int or values["version"] != 1:
        raise ValueError("version: expected 1")
    architecture = _architecture(values.get("architecture", {}))
    invariants = {key: Invariant(key, item["title"], item["status"], tuple(item.get("tests", [])),
                                 item.get("stage"))
                  for key, item in values.get("invariants", {}).items()}
    return Declaration(architecture, _entries(values.get("modules", {}), architecture),
                       _exemptions(values.get("exemptions", [])),
                       frozenset(values.get("cycles", {}).get("members", [])), invariants)


def module_name(relative: PurePath) -> str:
    """Use path parts, so nested Python modules have identical names on Windows and POSIX."""
    parts = relative.with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def code_modules(package: Path) -> dict[str, Path]:
    """List code, counting nested package initializers but excluding the root initializer."""
    return {module_name(path.relative_to(package)): path for path in sorted(package.rglob("*.py"))
            if path != package / "__init__.py"}


def _matches(prefix, name):
    return name == prefix or name.startswith(prefix + ".")


def classify(declaration: Declaration, name: str) -> Entry:
    """Prefer exact entries, then the longest wildcard prefix (including the package itself)."""
    if name in declaration.modules:
        return declaration.modules[name]
    candidates = [entry for key, entry in declaration.modules.items()
                  if key.endswith(".*") and _matches(key[:-2], name)]
    if not candidates:
        raise KeyError(name)
    return max(candidates, key=lambda entry: len(entry.name))


def undeclared(declaration: Declaration, modules: Iterable[str]) -> list[str]:
    """Report module names that have no owner."""
    missing = []
    for name in modules:
        try:
            classify(declaration, name)
        except KeyError:
            missing.append(name)
    return sorted(missing)


def undeclared_message(names: list[str]) -> str:
    """Point a new module's author to the declaration that needs review."""
    return f"未登记模块：{', '.join(names)}；请评审并更新 storyloop/architecture.toml"


def stale_entries(declaration: Declaration, modules: Iterable[str]) -> list[str]:
    """Report deleted exact entries and wildcard prefixes matching no current module."""
    names = set(modules)
    return sorted(key for key in declaration.modules
                  if (not any(_matches(key[:-2], name) for name in names) if key.endswith(".*")
                      else key not in names))
