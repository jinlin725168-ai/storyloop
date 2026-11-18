"""Requirement registry (link skeleton batch 2): stable requirement, rule and example IDs.

Each merged change writes one append-only record, openspec/requirements/changes/<change_id>.json,
on its feature branch before merge. The current registry is never stored: it is the replay of
those records in the order their files first appeared on the branch (merge order), so parallel
branches never conflict in Git and the history is the records themselves.

IDs are assigned by the engine when a contract is approved: `<change_id>.S<n>` for a requirement
(user story), `.R<n>` for a rule, `.E<n>` for an example (acceptance criterion). A contract points
at an existing ID to modify it; anything else is new. Modifying records the version it was based
on, and the record is rechecked against the target branch before it is written, so a concurrent
change to the same item stops for a human instead of being overwritten.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess

from .models import StoryLoopError, require

RECORDS = "openspec/requirements/changes"
MAP_PATH = "openspec/requirements/map.json"
STABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}\.[SRE][1-9][0-9]{0,3}$")
KINDS = {"requirements": "S", "rules": "R", "examples": "E"}


def stable_id(value: object, kind: str) -> bool:
    return (isinstance(value, str) and STABLE_ID.fullmatch(value) is not None
            and value.rsplit(".", 1)[1][0] == KINDS[kind])


def replay(project: Path, ref: str = "HEAD") -> dict:
    """The registry at `ref`: {requirements, rules, examples} of {id: entry} with version history."""
    registry: dict = {kind: {} for kind in KINDS}
    for path in _record_paths(project, ref):
        shown = _git(project, "show", f"{ref}:{path}")
        if shown is None:
            continue
        apply_record(registry, json.loads(shown))
    return registry


def apply_record(registry: dict, record: dict) -> None:
    """Fold one change's record into the registry: created items start at version 1, a content
    change bumps the version, `retired` keeps the ID forever and never reassigns it."""
    change_id = record["change_id"]
    for kind in KINDS:
        for item in record.get(kind, []):
            current = registry[kind].get(item["id"])
            if item["op"] == "created":
                registry[kind][item["id"]] = {**item["content"], "version": 1, "status": "active",
                                              "history": [{"version": 1, "change": change_id, "op": "created"}]}
            elif item["op"] == "modified" and current:
                current.update(item["content"])
                current["version"] += 1
                current["history"].append({"version": current["version"], "change": change_id,
                                           "op": "modified"})
            elif item["op"] == "retired" and current:
                current["status"] = "retired"
                current["history"].append({"version": current["version"], "change": change_id,
                                           "op": "retired"})
    for rule_id in record.get("fixes", []):
        rule = registry["rules"].get(rule_id)
        if rule is not None:
            rule.setdefault("fixes", []).append(change_id)


def plan(product: dict, change_id: str, registry: dict, previous: dict | None = None) -> dict:
    """Assign stable IDs at approval. Numbers already given to this change are kept on revision.

    Returns {"ids": {stories, rules, examples}, "record": <the record this change will write>}.
    """
    previous = previous or {}
    numbers = {kind: _taken(previous.get(kind, {}), change_id, KINDS[kind]) for kind in KINDS}
    ids: dict = {kind: {} for kind in KINDS}
    record: dict = {"version": 1, "change_id": change_id, **{kind: [] for kind in KINDS}}

    def assign(kind: str, key: str, existing: object, content: dict) -> str:
        if existing is not None:
            current = registry[kind].get(existing)
            require(current is not None and current["status"] == "active",
                    f"{existing} is not an active {kind[:-1]} in the requirement registry")
            same = all(current.get(field) == value for field, value in content.items())
            record[kind].append({"id": existing, "op": "referenced" if same else "modified",
                                 "base_version": current["version"], "content": content})
            ids[kind][key] = existing
            return existing
        new = previous.get(kind, {}).get(key)
        if not (new and new.startswith(change_id + ".")):
            numbers[kind] += 1
            new = f"{change_id}.{KINDS[kind]}{numbers[kind]}"
        record[kind].append({"id": new, "op": "created", "base_version": None, "content": content})
        ids[kind][key] = new
        return new

    for story in product["stories"]:
        content = {"title": story["title"], "as_a": story["as_a"], "want": story["want"],
                   "so_that": story["so_that"]}
        # The module is a requirement's attribute: it changes only when a contract names one.
        if product.get("capability") or not story.get("requirement"):
            content["module"] = product.get("capability") or change_id.lower()
        requirement = assign("requirements", story["id"], story.get("requirement"), content)
        rule_ids = story.get("rule_ids") or ["new"] * len(story["rules"])
        rules = []
        for index, (text, existing) in enumerate(zip(story["rules"], rule_ids), 1):
            rules.append(assign("rules", f"{story['id']}#{index}", None if existing == "new" else existing,
                                {"text": text, "requirement": requirement}))
        for criterion in story["acceptance"]:
            rule = rules[criterion["rule"] - 1] if isinstance(criterion.get("rule"), int) else None
            assign("examples", criterion["id"], criterion.get("example"), {
                "given": criterion["given"], "when": criterion["when"], "then": criterion["then"],
                "row_type": criterion.get("row_type", "positive"), "rule": rule,
                "requirement": requirement})
    for retired in product.get("retire") or []:
        kind = next(kind for kind in KINDS if stable_id(retired, kind))
        current = registry[kind].get(retired)
        require(current is not None and current["status"] == "active",
                f"Cannot retire {retired}: not an active {kind[:-1]}")
        record[kind].append({"id": retired, "op": "retired", "base_version": current["version"],
                             "content": {}})
    defect = product.get("defect") or {}
    record["fixes"] = [rule for rule in defect.get("rules") or [] if isinstance(rule, str)]
    return {"ids": ids, "record": record}


def conflicts(record: dict, registry: dict) -> list[str]:
    """Items this change modifies or retires whose version moved on (or that were retired)."""
    problems = []
    for kind in KINDS:
        for item in record.get(kind, []):
            if item["op"] == "created":
                if item["id"] in registry[kind]:
                    problems.append(f"{item['id']} already exists")
                continue
            current = registry[kind].get(item["id"])
            if current is None or current["status"] != "active":
                problems.append(f"{item['id']} is no longer active")
            elif current["version"] != item["base_version"]:
                problems.append(f"{item['id']} is at version {current['version']}, "
                                f"this change was based on {item['base_version']}")
    return problems


def write_record(project: Path, record: dict, target_ref: str) -> str:
    """Recheck against the target branch, then write the record; returns its repository path."""
    problems = conflicts(record, replay(project, target_ref))
    if problems:
        raise StoryLoopError("Requirement registry changed since approval: " + "; ".join(problems)
                             + ". Revise the contract on the new versions and approve again",
                             details={"registry_conflicts": problems})
    path = f"{RECORDS}/{record['change_id']}.json"
    target = Path(project) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                      encoding="utf-8")
    return path


def _taken(assigned: dict, change_id: str, letter: str) -> int:
    prefix = f"{change_id}.{letter}"
    return max([int(value[len(prefix):]) for value in assigned.values()
                if isinstance(value, str) and value.startswith(prefix)], default=0)


def _record_paths(project: Path, ref: str) -> list[str]:
    log = _git(project, "log", "--reverse", "--diff-filter=A", "--format=", "--name-only", ref, "--", RECORDS)
    seen, ordered = set(), []
    for line in (log or "").splitlines():
        if line.endswith(".json") and line not in seen:
            seen.add(line)
            ordered.append(line)
    return ordered


def _git(project: Path, *args: str) -> str | None:
    result = subprocess.run(["git", *args], cwd=project, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    return result.stdout if result.returncode == 0 else None


def registry_view(project: Path, identifier: str | None = None) -> dict:
    """The replayed registry for `storyloop requirements`: requirements with their rules nested,
    versions, status and the fixes that named each rule; or one item's full history."""
    registry = replay(project)
    if identifier is not None:
        kind = next((kind for kind in KINDS if stable_id(identifier, kind)), None)
        require(kind is not None and identifier in registry[kind], f"Unknown registry ID {identifier}")
        return {"status": "REQUIREMENTS", "item": {"id": identifier, "kind": kind[:-1],
                                                    **registry[kind][identifier]}}
    rules_of: dict = {}
    for rule_id, rule in registry["rules"].items():
        rules_of.setdefault(rule.get("requirement"), []).append({"id": rule_id, **rule})
    requirements = []
    for requirement_id, requirement in registry["requirements"].items():
        rules = rules_of.get(requirement_id, [])
        requirements.append({"id": requirement_id, **requirement, "rules": rules,
                             "fixes": sorted({fix for rule in rules for fix in rule.get("fixes", [])})})
    return {"status": "REQUIREMENTS", "requirements": requirements}


def render_view(result: dict) -> str:
    if "item" in result:
        item = result["item"]
        lines = [f"{item['id']}（{item['kind']}，v{item['version']}，{item['status']}）"]
        lines += [f"  v{entry['version']} {entry['op']} by {entry['change']}" for entry in item["history"]]
        if item.get("fixes"):
            lines.append("  fix：" + ", ".join(item["fixes"]))
        return "\n".join(lines)
    if not result["requirements"]:
        return "需求登记为空：合入的变更还没有写入登记记录"
    lines = []
    for requirement in result["requirements"]:
        fixes = f"，{len(requirement['fixes'])} 个 fix" if requirement["fixes"] else ""
        lines.append(f"{requirement['id']} v{requirement['version']} {requirement['title']}"
                     f"（{requirement['status']}{fixes}）")
        for rule in requirement["rules"]:
            lines.append(f"  {rule['id']} v{rule['version']} {rule['text']}"
                         + ("（已退役）" if rule["status"] == "retired" else ""))
    return "\n".join(lines)


def compact(project: Path, limit: int = 200) -> list[dict] | dict:
    """Active requirements with their rules (bounded) and, when a map exists, its modules."""
    rows = _compact_rows(project, limit)
    path = Path(project) / MAP_PATH
    if not path.is_file():
        return rows
    value = json.loads(path.read_text(encoding="utf-8"))
    return {"modules": [{key: module[key] for key in ("id", "name", "responsibility")}
                        for module in value["modules"] if module.get("status", "active") == "active"],
            "requirements": rows}


def _compact_rows(project: Path, limit: int) -> list[dict]:
    view = registry_view(project)["requirements"]
    rows = [{"id": item["id"], "version": item["version"], "title": item["title"],
             "module": item.get("module"),
             "rules": [{"id": rule["id"], "version": rule["version"], "text": rule["text"]}
                       for rule in item["rules"] if rule["status"] == "active"]}
            for item in view if item["status"] == "active"]
    return rows[:limit]
