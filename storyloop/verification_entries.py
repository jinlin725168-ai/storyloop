"""Deterministic links from approved public entry to oracle and design paths."""

import ast
from pathlib import Path
import re

from .models import StoryLoopError, fields, identifier, require, text
from .path_security import repository_relative
from .processutil import redact
from .smoke import suite_root


def new_format(product: dict) -> bool:
    """The story field itself is the format marker; old contracts stay old."""
    return any("verification_entries" in story for story in product.get("stories", []))


def entry_owners(product: dict) -> dict[str, str]:
    return {entry["id"]: story["id"] for story in product["stories"]
            for entry in story.get("verification_entries", [])}


def check_entry_use(project: Path, change: dict, manifest: dict) -> None:
    """Reject an uninvoked approved entry before RED execution, with repairable evidence."""
    if not new_format(change["product"]):
        return
    root = suite_root(project, change["change_id"])
    sources = {path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
               for path in root.rglob("*.py") if path.is_file()}
    findings = entry_use_findings(change["product"], manifest, sources)
    if not findings:
        return
    diagnostics = [{"case_id": item["test_id"], "kind": "error",
                    "message": redact(item["message"])[:2000],
                    "traceback_tail": f"{item['path']}:{item['line']}"}
                   for item in findings[:20]]
    raise StoryLoopError("Acceptance test does not invoke its declared verification entry",
                         code="ORACLE_DEFECT", details={
                             "fixture_repairable": True, "defect_category": findings[0]["category"],
                             "test_ids": sorted({item["test_id"] for item in findings}),
                             "entries": [item["entry"] for item in findings],
                             "diagnostics": diagnostics})


def entry_use_findings(product: dict, manifest: dict, sources: dict[str, str]) -> list[dict]:
    """Check an explicit test source for a real public-entry call, never a textual mention."""
    if not new_format(product):
        return []
    entries = {entry["id"]: entry for story in product["stories"]
               for entry in story.get("verification_entries", [])}
    findings = []
    parsed, syntax = {}, {}
    for path, body in sources.items():
        if path.endswith(".py"):
            try:
                parsed[path] = ast.parse(body, filename=path)
            except SyntaxError as exc:
                syntax[path] = exc.lineno or 1
    for test in manifest["tests"]:
        selected = _command_sources(test["command"], parsed)
        broken_source = next((path for path in syntax if any(
            arg == path or arg.replace("\\", "/").endswith("/" + path)
            for arg in test["command"])), None)
        for entry_id in test.get("entry_ids", []):
            entry = entries[entry_id]
            if entry["kind"] not in {"cli", "stop_json", "public_function"}:
                continue
            if broken_source:
                findings.append({"test_id": test["id"], "entry": entry,
                                 "path": broken_source, "line": syntax[broken_source],
                                 "category": "CASE_OR_FIXTURE_ERROR",
                                 "message": f"Test {test['id']} has invalid Python syntax"})
                continue
            if any(_uses_entry(entry, tree) for tree in selected.values()):
                continue
            path = next(iter(selected), "manifest.json")
            findings.append({"test_id": test["id"], "entry": entry,
                             "path": path, "line": 1,
                             "category": "UNVERIFIED_ENTRY_USE",
                             "message": f"Test {test['id']} does not invoke declared entry {entry_id}"})
    return findings


def _command_sources(command: list[str], parsed: dict[str, ast.AST]) -> dict[str, ast.AST]:
    selected = {path: tree for path, tree in parsed.items()
                if any(arg == path or arg.replace("\\", "/").endswith("/" + path)
                       for arg in command)}
    pending = list(selected)
    while pending:
        tree = selected[pending.pop()]
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                    for alias in node.names]
        for name in imports:
            for path, candidate in parsed.items():
                if path not in selected and (path == name.replace(".", "/") + ".py" or
                                             path.endswith("/" + name.replace(".", "/") + ".py")):
                    selected[path] = candidate
                    pending.append(path)
    return selected


def _uses_entry(entry: dict, tree: ast.AST) -> bool:
    if entry["kind"] in {"cli", "stop_json"}:
        match = re.search(r"\bstoryloop\s+([a-z][a-z0-9-]*)", entry["locator"])
        return bool(match and _calls_cli(tree, match.group(1)))
    choices = [part.strip() for part in entry["locator"].split(" / ")]
    module, dot, first = choices[0].rpartition(".")
    return bool(dot and any(_calls_function(tree, module, symbol)
                            for symbol in [first, *choices[1:]]))


def _calls_cli(tree: ast.AST, subcommand: str) -> bool:
    modules = {alias.asname or alias.name for node in ast.walk(tree)
               if isinstance(node, ast.Import) for alias in node.names if alias.name == "subprocess"}
    functions = {alias.asname or alias.name for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and node.module == "subprocess"
                 for alias in node.names if alias.name in {"run", "Popen", "check_call", "check_output"}}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        call = node.func
        direct = isinstance(call, ast.Name) and call.id in functions
        qualified = (isinstance(call, ast.Attribute) and call.attr in
                     {"run", "Popen", "check_call", "check_output"}
                     and isinstance(call.value, ast.Name) and call.value.id in modules)
        if not (direct or qualified) or not isinstance(node.args[0], (ast.List, ast.Tuple)):
            continue
        tokens = [value.value if isinstance(value, ast.Constant) and isinstance(value.value, str)
                  else None for value in node.args[0].elts]
        if any(tokens[i:i + 2] == ["storyloop", subcommand] or
               tokens[i:i + 3] == ["-m", "storyloop", subcommand]
               for i in range(len(tokens) - 1)):
            return True
    return False


def _calls_function(tree: ast.AST, module: str, symbol: str) -> bool:
    direct = {alias.asname or alias.name for node in ast.walk(tree)
              if isinstance(node, ast.ImportFrom) and node.module == module
              for alias in node.names if alias.name == symbol}
    modules = {alias.asname or alias.name for node in ast.walk(tree)
               if isinstance(node, ast.Import) for alias in node.names if alias.name == module}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in direct:
            return True
        if (isinstance(node.func, ast.Attribute) and node.func.attr == symbol
                and isinstance(node.func.value, ast.Name) and node.func.value.id in modules):
            return True
        if (module in {alias.name for item in ast.walk(tree) if isinstance(item, ast.Import)
                       for alias in item.names}
                and ast.unparse(node.func) == f"{module}.{symbol}"):
            return True
    return False


def validate_entry_paths(option: dict, product: dict, manifest: dict) -> None:
    """Require the selected option to cover each oracle-used automatic entry and AC."""
    if not new_format(product):
        return
    owners = entry_owners(product)
    criterion_stories = {criterion["id"]: story["id"] for story in product["stories"]
                         for criterion in story["acceptance"] if criterion["kind"] == "automated"}
    paths = set(option["touched"] + option["creates"])
    rows = option.get("entry_paths")
    require(isinstance(rows, list) and bool(rows), "Selected option needs entry_paths")
    covered = set()
    for row in rows:
        fields(row, {"entry_id", "criterion_ids", "path", "via"})
        entry_id = identifier(row["entry_id"], "entry path id")
        require(entry_id in owners, "Plan entry_paths references unknown verification entry")
        criteria = row["criterion_ids"]
        require(isinstance(criteria, list) and bool(criteria)
                and all(isinstance(item, str) for item in criteria)
                and len(criteria) == len(set(criteria)), "Plan entry_paths needs unique criterion_ids")
        for criterion_id in criteria:
            require(criterion_id in criterion_stories and criterion_stories[criterion_id] == owners[entry_id],
                    "Plan entry_paths references a cross-story or manual criterion")
            covered.add((criterion_id, entry_id))
        path = repository_relative(row["path"], "entry path")
        require(path in paths, "Plan entry path is outside touched/creates")
        text(row["via"], "entry path via")
    required = set()
    for test in manifest["tests"]:
        for criterion_id in test["criteria"]:
            require(criterion_id in criterion_stories, "Oracle references unknown automatic criterion")
            for entry_id in test["entry_ids"]:
                require(entry_id in owners, "Oracle references unknown verification entry")
                if owners[entry_id] == criterion_stories[criterion_id]:
                    required.add((criterion_id, entry_id))
    require(required <= covered, "Plan entry_paths does not cover every oracle-used entry and AC")
