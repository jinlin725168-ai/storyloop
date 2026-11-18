"""Requirements map and bootstrap (spec-craft batch 4, link skeleton §9).

An existing project's living specs predate the registry. `map-propose` asks the read-only survey
role to draft a module map (id, name, responsibility, boundaries, code areas) and to place each
existing requirement in a module; the engine validates the draft and stores it with a digest.
`map-apply --proposal <digest>` is the human confirmation: it writes openspec/requirements/map.json
and one bootstrap registry record that gives every existing requirement and rule a stable ID under
the migration change `REQ-<date>-requirements-bootstrap`, and commits both. Agents propose, the
human installs — the same pattern as smoke-propose / smoke-apply.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

from .models import require, text
from .requirements_registry import MAP_PATH, RECORDS, replay
from .storage import load_json, write_json

DRAFT = Path(".storyloop/drafts/requirements-map/proposal.json")
MODULE_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
REQUIREMENT = re.compile(r"^### Requirement: (.+?)\s*$")
RULE_PREFIX = re.compile(r"^\[[^\]]+\]\s*")


def load_map(project: Path) -> dict | None:
    path = Path(project) / MAP_PATH
    return load_json(path) if path.is_file() else None


def module_ids(project: Path) -> set[str] | None:
    """Active module IDs, or None when the project has no map yet."""
    value = load_map(project)
    if value is None:
        return None
    return {item["id"] for item in value["modules"] if item.get("status", "active") == "active"}


def existing_requirements(project: Path) -> list[dict]:
    """Requirements in the living specs that the registry does not know yet, with their SHALL
    rules (the lines between a requirement heading and its first scenario)."""
    registered = {item.get("title") for item in replay(project)["requirements"].values()}
    found = []
    for spec in sorted((Path(project) / "openspec/specs").glob("*/spec.md")):
        current, in_rules = None, False
        for line in spec.read_text(encoding="utf-8").splitlines():
            match = REQUIREMENT.match(line)
            if match:
                current, in_rules = {"spec": spec.parent.name, "title": match.group(1), "rules": []}, True
                found.append(current)
            elif line.startswith("#### Scenario:"):
                in_rules = False
            elif current is not None and in_rules and "SHALL" in line.upper():
                current["rules"].append(RULE_PREFIX.sub("", line.strip()))
    return [item for item in found if item["title"] not in registered]


def validate_map(project: Path, answer: dict, requirements: list[dict]) -> dict:
    """Engine-side checks of the survey role's draft; returns the normalized map."""
    modules = answer.get("modules")
    require(isinstance(modules, list) and modules, "The map needs at least one module")
    seen = set()
    for module in modules:
        require(isinstance(module, dict) and MODULE_ID.fullmatch(str(module.get("id"))) is not None,
                "Module id is a lowercase slug")
        require(module["id"] not in seen, f"Duplicate module {module['id']}")
        seen.add(module["id"])
        for field in ("name", "responsibility", "boundaries"):
            text(module.get(field), f"module {field}")
        areas = module.get("code_areas")
        require(isinstance(areas, list) and areas, f"Module {module['id']} names its code areas")
        for area in areas:
            require(isinstance(area, str) and (Path(project) / area).exists(),
                    f"Module {module['id']} code area does not exist: {area}")
    placed = {(item.get("spec"), item.get("title")): item.get("module") for item in answer.get("assignments") or []}
    for requirement in requirements:
        module = placed.get((requirement["spec"], requirement["title"]))
        require(module in seen, f"Requirement {requirement['title']!r} ({requirement['spec']}) needs a module")
    return {"version": 1,
            "modules": [{"id": m["id"], "name": m["name"], "responsibility": m["responsibility"],
                         "boundaries": m["boundaries"], "code_areas": list(m["code_areas"]),
                         "status": "active", "derived_from": None} for m in modules],
            "assignments": [{"spec": r["spec"], "title": r["title"],
                             "module": placed[(r["spec"], r["title"])]} for r in requirements]}


def propose_map(project: Path, invoke) -> dict:
    """Draft the map with the survey role and store it for human review; nothing tracked changes."""
    project = Path(project).resolve()
    requirements = existing_requirements(project)
    from .baseline import load_project_config
    profile = load_project_config(project).get("profile") or {}
    answer = invoke(project, "survey", {"mode": "requirements-map", "existing_requirements": requirements,
                                        "module_roots": profile.get("module_roots") or []})
    proposal = {"map": validate_map(project, answer, requirements), "requirements": requirements}
    digest = hashlib.sha256(json.dumps(proposal, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    write_json(project / DRAFT, {**proposal, "digest": digest}, max_bytes=2_000_000)
    return {"status": "MAP_PROPOSED", "digest": digest, "draft": DRAFT.as_posix(),
            "modules": [m["id"] for m in proposal["map"]["modules"]],
            "next": f"检查 {DRAFT.as_posix()} 后：storyloop map-apply {project} --proposal {digest}"}


def apply_map(project: Path, digest: str, *, now=None) -> dict:
    """Human confirmation: install the reviewed map and the bootstrap record, then commit."""
    project = Path(project).resolve()
    require((project / DRAFT).is_file(), "No requirements map proposal: run storyloop map-propose first")
    draft = load_json(project / DRAFT)
    require(draft.get("digest") == digest, "Proposal digest does not match the reviewed draft")
    require(not _git(project, "status", "--porcelain").strip(), "Working tree must be clean")
    moment = (now or (lambda: datetime.now(timezone.utc)))()
    change_id = f"REQ-{moment:%Y%m%d}-requirements-bootstrap"
    require(not (project / RECORDS / f"{change_id}.json").exists(), f"{change_id} already exists")
    record = _bootstrap_record(change_id, draft)
    write_json(project / MAP_PATH, draft["map"], max_bytes=2_000_000)
    path = f"{RECORDS}/{change_id}.json"
    (project / path).parent.mkdir(parents=True, exist_ok=True)
    (project / path).write_text(json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                encoding="utf-8")
    _git(project, "add", MAP_PATH, path)
    _git(project, "commit", "-q", "-m", f"docs({change_id}): requirements map and bootstrap")
    (project / DRAFT).unlink()
    return {"status": "MAP_APPLIED", "change_id": change_id, "paths": [MAP_PATH, path],
            "requirements": len(record["requirements"]), "rules": len(record["rules"])}


def _bootstrap_record(change_id: str, draft: dict) -> dict:
    module_of = {(item["spec"], item["title"]): item["module"] for item in draft["map"]["assignments"]}
    record = {"version": 1, "change_id": change_id, "requirements": [], "rules": [], "examples": [],
              "fixes": [], "bootstrap": True}
    rule_number = 0
    for number, requirement in enumerate(draft["requirements"], 1):
        requirement_id = f"{change_id}.S{number}"
        record["requirements"].append({"id": requirement_id, "op": "created", "base_version": None, "content": {
            "title": requirement["title"], "module": module_of[(requirement["spec"], requirement["title"])],
            "source": f"openspec/specs/{requirement['spec']}/spec.md"}})
        for rule in requirement["rules"]:
            rule_number += 1
            record["rules"].append({"id": f"{change_id}.R{rule_number}", "op": "created", "base_version": None,
                                    "content": {"text": rule, "requirement": requirement_id}})
    return record


def _git(project: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=project, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout
