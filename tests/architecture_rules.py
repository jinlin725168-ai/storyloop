"""Pure architecture analysis; declarations are consumed by tests only."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePath
import ast
import re
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "storyloop"
DECLARATION = PACKAGE / "architecture.toml"
MODULES_MD = ROOT / "docs" / "modules.md"
ZONES = ("base", "mutable")
SPLIT_STAGES = ("B", "D")
EXEMPTION_STAGES = ("B", "C", "D")
INVARIANT_STAGES = ("B", "E", "T6")
FORBIDDEN_GIT = ("push", "rebase", "filter-branch", "--amend", "--force",
                 "--force-with-lease", "--hard", "update-ref")


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
    return Declaration(architecture, _entries(values.get("modules", {}), architecture),
                       _exemptions(values.get("exemptions", [])),
                       frozenset(values.get("cycles", {}).get("members", [])),
                       _invariants(values.get("invariants", {})))


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


@dataclass(frozen=True)
class Violation:
    source: str
    target: str
    rules: tuple[str, ...]


def _import_targets(node, package_parts, known):
    if isinstance(node, ast.Import):
        return {alias.name[len("storyloop."):] for alias in node.names
                if alias.name.startswith("storyloop.")}
    if not isinstance(node, ast.ImportFrom):
        return set()
    if node.level:
        if node.level > len(package_parts) + 1:
            return set()
        base = package_parts[:len(package_parts) - node.level + 1]
        prefix = ".".join([*base, *node.module.split(".")] if node.module else base)
    elif node.module == "storyloop":
        prefix = ""
    elif node.module and node.module.startswith("storyloop."):
        prefix = node.module[len("storyloop."):]
    else:
        return set()
    result = set()
    for alias in node.names:
        child = ".".join(filter(None, (prefix, alias.name)))
        result.add(child if child in known else prefix)
    return result


def import_graph(package: Path) -> dict[str, set[str]]:
    """Resolve all static imports, including local functions and TYPE_CHECKING blocks."""
    modules = code_modules(package)
    graph = {}
    for name, path in modules.items():
        parts = name.split(".")
        parent = parts if path.name == "__init__.py" else parts[:-1]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        targets = set().union(*(_import_targets(node, parent, modules) for node in ast.walk(tree)))
        graph[name] = (targets & modules.keys()) - {name}
    return graph


def violations(declaration: Declaration, graph: dict[str, set[str]]) -> list[Violation]:
    """Flag upward edges and every base-to-mutable edge, including same-rank ones."""
    found = []
    for source in sorted(graph):
        for target in sorted(graph[source]):
            left, right = classify(declaration, source), classify(declaration, target)
            rules = []
            if declaration.architecture[left.module].rank < declaration.architecture[right.module].rank:
                rules.append("upward")
            if left.zone == "base" and right.zone == "mutable":
                rules.append("base_imports_mutable")
            if rules:
                found.append(Violation(source, target, tuple(rules)))
    return found


def unexempted(declaration: Declaration, found: list[Violation]) -> list[Violation]:
    """Match exemptions by the exact source/target edge."""
    allowed = {(item.source, item.target) for item in declaration.exemptions}
    return [item for item in found if (item.source, item.target) not in allowed]


def stale_exemptions(declaration: Declaration, found: list[Violation]) -> list[Exemption]:
    """Force removed dependencies to be removed from the reviewed exemption list."""
    current = {(item.source, item.target) for item in found}
    return [item for item in declaration.exemptions if (item.source, item.target) not in current]


def _strong_components(graph):
    """Tarjan traversal; return components separately so measurements retain their sizes."""
    indices, low, stack, active, components = {}, {}, [], set(), []

    def visit(node):
        indices[node] = low[node] = len(indices)
        stack.append(node)
        active.add(node)
        for target in sorted(graph.get(node, ())):
            if target not in indices:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in active:
                low[node] = min(low[node], indices[target])
        if low[node] == indices[node]:
            component = []
            while True:
                member = stack.pop()
                active.remove(member)
                component.append(member)
                if member == node:
                    break
            components.append(tuple(sorted(component)))

    before = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(max(10000, before))
        for node in sorted(graph):
            if node not in indices:
                visit(node)
    finally:
        sys.setrecursionlimit(before)
    return components


def cycle_members(graph: dict[str, set[str]]) -> set[str]:
    """Return members of strongly connected components containing at least two modules."""
    return {member for component in _strong_components(graph) if len(component) > 1
            for member in component}


def _invariants(values):
    result = {}
    for key, raw in _table(values, "invariants").items():
        item = _table(raw, f"invariants.{key}")
        status = _choice(item.get("status"), ("guarded", "pending"), f"invariants.{key}.status")
        tests = item.get("tests", [])
        if (not isinstance(tests, list) or (status == "guarded" and not tests)
                or any(not isinstance(test, str) or not test.strip() for test in tests)):
            raise ValueError(f"invariants.{key}.tests: guarded invariant needs test IDs")
        stage = item.get("stage")
        if status == "pending" or stage is not None:
            _choice(stage, INVARIANT_STAGES, f"invariants.{key}.stage")
        result[key] = Invariant(key, _text(item.get("title"), f"invariants.{key}.title"),
                                status, tuple(tests), stage)
    return result


def _testcase_classes(tree):
    """Resolve unittest aliases and local inheritance without executing the referenced file."""
    aliases, bases = set(), set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            aliases.update(alias.asname or alias.name for alias in node.names if alias.name == "unittest")
        if isinstance(node, ast.ImportFrom) and node.module == "unittest":
            bases.update(alias.asname or alias.name for alias in node.names if alias.name == "TestCase")
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    found = set()
    while True:
        inherited = {name for name, node in classes.items() if any(
            (isinstance(base, ast.Name) and base.id in bases | found)
            or (isinstance(base, ast.Attribute) and base.attr == "TestCase"
                and isinstance(base.value, ast.Name) and base.value.id in aliases)
            for base in node.bases)}
        if inherited <= found:
            return [classes[name] for name in sorted(found)]
        found.update(inherited)


def resolve_test(test_id: str, root: Path = ROOT) -> bool:
    """Resolve exactly one TestCase method from a test ID, using AST only."""
    if not isinstance(test_id, str) or test_id.count("::") != 1:
        return False
    module, method = test_id.split("::")
    parts = module.split(".")
    if parts[0] != "tests" or not all(part.isidentifier() for part in [*parts, method]):
        return False
    try:
        source = root.joinpath(*parts).with_suffix(".py").read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeError):
        return False
    return sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method
               for cls in _testcase_classes(tree) for node in cls.body) == 1


def _argument_values(nodes):
    values = []
    for node in nodes:
        if isinstance(node, ast.Starred) and isinstance(node.value, (ast.List, ast.Tuple)):
            values.extend(_argument_values(node.value.elts))
        else:
            values.append(node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None)
    return values


def _forbidden_operation(values):
    position = 0
    while position < len(values):
        value = values[position]
        if value in {"-c", "-C", "--git-dir", "--work-tree", "--namespace"}:
            position += 2
        elif isinstance(value, str) and value.startswith("-"):
            position += 1
        else:
            break
    if position >= len(values) or values[position] is None:
        return []
    command, arguments = values[position], values[position + 1:]
    found = [command] if command in {"push", "rebase", "filter-branch", "update-ref"} else []
    cleanup = command == "worktree" and arguments[:1] == ["remove"]
    position = 0
    while position < len(arguments):
        value = arguments[position]
        if value == "--":
            break
        if command == "commit" and value in {"-m", "--message", "-F", "--file"}:
            position += 2
            continue
        if ((command == "commit" and value == "--amend")
                or (command == "reset" and value == "--hard")):
            found.append(value)
        elif not cleanup and value in {"--force", "-f"}:
            found.append("--force")
        elif isinstance(value, str) and value.split("=", 1)[0] == "--force-with-lease":
            found.append("--force-with-lease")
        position += 1
    return found


def forbidden_git_calls(source: str) -> list[str]:
    """Inspect literal Git call arguments, including aliases; never execute source."""
    tree = ast.parse(source)
    git_names = {"_git"}
    process_names = {"run_process", "run", "call", "check_call", "check_output", "Popen"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            git_names.update(alias.asname or alias.name for alias in node.names if alias.name == "_git")
            process_names.update(alias.asname or alias.name for alias in node.names
                                 if alias.name in process_names)
    calls = sorted((node for node in ast.walk(tree) if isinstance(node, ast.Call)),
                   key=lambda node: (node.lineno, node.col_offset))
    found = []
    for call in calls:
        name = call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", None)
        if name in git_names:
            found.extend(_forbidden_operation(_argument_values(call.args[1:])))
        elif name in process_names:
            argv = call.args[0] if call.args else next(
                (kw.value for kw in call.keywords if kw.arg in {"args", "argv", "command"}), None)
            if isinstance(argv, (ast.List, ast.Tuple)):
                values = _argument_values(argv.elts)
                if values[:1] == ["git"]:
                    found.extend(_forbidden_operation(values[1:]))
    return found


def modules_md_rows(text: str) -> dict[str, tuple[str, str]]:
    """Read module names, inherited architecture titles and zones from the inventory."""
    rows, title = {}, ""
    has_section = any(line.startswith("## ") and "模块清单" in line for line in text.splitlines())
    active = not has_section
    for line in text.splitlines():
        if has_section and line.startswith("## "):
            if "模块清单" in line:
                active = True
            elif active:
                break
        if not active or not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 5:
            continue
        names = re.findall(r"`([A-Za-z_]\w*(?:\.\w+)*)`", cells[1])
        if not names:
            continue
        title = cells[0] or title
        if not title:
            raise ValueError(f"{names[0]}: missing architecture title")
        for name in names:
            if name in rows:
                raise ValueError(f"duplicate module: {name}")
            rows[name] = (title, cells[2])
    return rows
