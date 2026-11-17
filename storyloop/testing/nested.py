"""Nested-project kit for acceptance suites of StoryLoop's own mechanisms (roadmap C4).

``nested_case`` builds a small real repository with a committed application, unit
tests, a protected smoke suite and an approved contract, then runs StoryLoop on it with
scripted roles. Acceptance suites for changes to StoryLoop's own mechanisms (retry loops,
gates, stops, routing) replace one role and observe the run through ``run()`` and ``state()``.
Every default answer is valid, so a test states only the behaviour it is about (roadmap C4).
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
import subprocess
import sys

RISKS = ("sensitive", "migration", "external_contract", "new_interface", "crosscut",
         "principle_deviation", "hard_verify_high_cost", "dependency_swap", "persistent_state",
         "human_choice", "guard")
PROFILE = {"module_roots": ["app", "modules"], "test_roots": ["tests"],
           "unit_commands": [["{python}", "-m", "unittest", "discover", "-s", "tests"]],
           "check_commands": [["{python}", "-c", "pass"]]}


@dataclass
class NestedCase:
    """A nested project with one approved change and the services that answer for its roles."""
    root: Path
    change_id: str
    services: object

    def run(self, **roles) -> dict:
        """Run (or resume) the change; keyword roles replace services for this run only."""
        from storyloop.project_loop import run_change
        return run_change(self.root, self.change_id, replace(self.services, **roles))

    def state(self) -> dict:
        from storyloop.project_loop import load_run_state
        return load_run_state(self.root, self.change_id)


def nested_case(root: Path, tier: str = "standard", **roles) -> NestedCase:
    """Build the nested project under ``root`` and approve one change at ``tier``.

    Roles: acceptance, implement, review, characterize, design; each is ``(project, context)``.
    """
    from storyloop.approvals import approve_change
    from storyloop.changes import create_change
    from storyloop.onboarding_cli import bootstrap_with_project_checks
    from storyloop.project_loop import LoopServices
    from storyloop.readiness import inspect_readiness
    if tier not in ("light", "standard", "heavy"):
        raise ValueError("Unknown routing tier")
    root = Path(root)
    _repository(root)
    bootstrap_with_project_checks(root, deepcopy(PROFILE))
    _offline_retrospective(root)
    ready = inspect_readiness(root)
    if ready["status"] != "READY":
        raise RuntimeError(f"Nested project is not ready: {ready['status']}")
    value = product()
    change = create_change(root, "orders", value)
    change = approve_change(root, change["change_id"], tier=None if tier == "light" else tier)
    identifier = change["change_id"]
    services = LoopServices(lambda project, context: acceptance(identifier), implement,
                            passing_review, characterize=characterize,
                            design=lambda project, context: plan(value))
    return NestedCase(root, identifier, replace(services, **roles))


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout


def product() -> dict:
    """A valid assessed contract: create modules/orders/service.py with VALUE equal to one."""
    evidence = {"locations": ["app/service.py:1"], "search_scope": ["app"]}
    return {"version": 1, "goal": "Provide order module version one while preserving service health",
            "non_goals": [], "questions": [], "stories": [{
                "id": "ST-1", "title": "Read order module version", "as_a": "local module consumer",
                "want": "read the order module version", "so_that": "I can select the expected behavior",
                "rules": ["The order module version is the integer one"], "out_of_scope": [],
                "depends_on": [], "acceptance": [{
                    "id": "AC-1", "given": "the order module is available",
                    "when": "its version constant is read", "then": "VALUE equals the integer one",
                    "kind": "automated"}]}],
            "assessment": {
                "verifiable_goal": True,
                "scope": {"touched": ["app/service.py"], "creates": ["modules/orders/service.py"],
                          "interfaces": [], "symbols": [{"path": "app/service.py", "symbol": "serve"}]},
                "risks": {key: {"value": "no", "evidence": deepcopy(evidence)} for key in RISKS},
                "criteria": [{"story_id": "ST-1", "criterion_id": "AC-1", "feasibility": "local_automated",
                              "existence": "absent",
                              "method": {"commands": [["{python}", "-m", "unittest"]], "steps": [],
                                         "expected": "Order VALUE is the integer one"},
                              "evidence": evidence}]}}


def plan(value: dict) -> dict:
    """A valid design answer implementing the contract's scope."""
    return {"main_option": "main", "options": [{
        "id": "main", "summary": "Implement the approved contract",
        **deepcopy(value["assessment"]["scope"]),
        "risks": deepcopy(value["assessment"]["risks"]), "forbidden_paths": [],
        "constraints": [{"criterion_id": "AC-1", "path": "app/service.py", "rule": "Preserve existing callers"}],
        "regression": ["Run the existing unit and acceptance suites"],
        "migration": {"steps": [], "reason": "No persisted data changes"},
        "dependencies": [], "principle_checks": []}],
        "questions": [], "design": "Implement AC-1 in the existing module."}


def acceptance(change_id: str) -> dict:
    """A protected acceptance suite that is red before and green after ``implement``."""
    source = ("import unittest\nimport runpy\nfrom pathlib import Path\n"
              "class Acceptance(unittest.TestCase):\n"
              "    def test_new_value(self):\n"
              "        path = Path('modules/orders/service.py')\n"
              "        self.assertTrue(path.is_file())\n"
              "        self.assertEqual(runpy.run_path(str(path))['VALUE'], 1)\n"
              "if __name__ == '__main__': unittest.main()\n")
    return {"files": [{"path": "flow.py", "content": source}],
            "manifest": {"version": 1, "tests": [{
                "id": "SMOKE-1", "criteria": ["AC-1"],
                "command": ["{python}", f"acceptance/changes/{change_id}/flow.py"],
                "timeout_seconds": 10}]}}


def implement(project: Path, context: dict) -> dict:
    target = Path(project) / "modules/orders/service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("VALUE = 1\n", encoding="utf-8")
    return {"summary": "Implement AC-1", "questions": []}


def characterize(project: Path, context: dict) -> dict:
    """Lock serve() in a new test file (a fresh name on each call)."""
    name, suffix = "tests/test_lock.py", 2
    while (Path(project) / name).exists():
        name, suffix = f"tests/test_lock_{suffix}.py", suffix + 1
    (Path(project) / name).write_text(
        "import unittest\nfrom app.service import serve\nclass Lock(unittest.TestCase):\n"
        "    def test_serve(self): self.assertEqual(serve(), 'ok')\n", encoding="utf-8")
    return {"summary": "Preserve serve behavior", "touched": ["app/service.py"], "tests": [name],
            "questions": [], "claims": [{"test": name, "behavior": "serve still returns the original value",
                                         "symbols": [], "criterion_ids": []}]}


def passing_review(project: Path, context: dict) -> dict:
    if context.get("mode") == "consistency":
        return {"verdict": "coherent", "summary": "ok", "findings": [], "questions": []}
    return {"verdict": "pass", "summary": "ok", "findings": [], "questions": []}


def _repository(root: Path) -> None:
    git(root, "init", "-q")
    git(root, "config", "user.email", "storyloop@example.invalid")
    git(root, "config", "user.name", "StoryLoop Test")
    for path, text in {
        "app/service.py": "def serve():\n    return 'ok'\n",
        "core.py": "STABLE = True\n",
        "tests/test_service.py": ("import unittest\nfrom app.service import serve\n"
                                  "class Service(unittest.TestCase):\n"
                                  "    def test_serve(self): self.assertEqual(serve(), 'ok')\n"),
        "acceptance/smoke/test_core.py": (
            "import unittest\nimport runpy\nclass Core(unittest.TestCase):\n"
            "    def test_core(self): self.assertEqual(runpy.run_path('app/service.py')['serve'](), 'ok')\n"
            "if __name__ == '__main__': unittest.main()\n"),
    }.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")
    from storyloop.storage import write_json
    write_json(root / "acceptance/smoke/manifest.json", {"version": 1, "tests": [{
        "id": "core", "description": "protected core smoke",
        "command": ["{python}", "acceptance/smoke/test_core.py"], "timeout_seconds": 10}]})
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")


def _offline_retrospective(root: Path) -> None:
    """Answer the automatic retrospective offline, so a run never reaches a real agent."""
    from storyloop.storage import load_json, write_json
    path = root / ".storyloop/config.json"
    config = load_json(path)
    script = ("import json,sys; request=json.load(sys.stdin); "
              "assert request['payload'].get('mode')=='retrospective', 'nested kit: scripted roles only'; "
              "print(json.dumps(dict(lessons=[],errata=[],uncovered_concepts=[],"
              "nothing_to_learn='Nested kit: nothing reusable')))")
    config.update(adapter="command", agent_command=[sys.executable, "-c", script])
    write_json(path, config)
