"""The architect role: a read-only design after RED that names the files to touch, feeding characterize,
implement and review; questions pause the run; it never writes the workspace."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

from storyloop.answers import record_answers
from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from storyloop.changes import create_change
from storyloop.models import StoryLoopError
from storyloop.storage import load_json, write_json
from storyloop.project_loop import LoopServices, load_run_state, run_change
from tests.support import product
from tests.test_baseline_behavior import git, repository
from tests.test_project_loop import smoke_payload

NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)
UNIT = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."]
PLAN = ("## Context\nOrders live in modules/orders.\n\n## Goals\nExpose VALUE.\n\n## Decisions\n"
        "- Choice: change service.py only\n\n## Files\n- modules/orders/service.py: holds VALUE\n\n"
        "## Risks And Trade-Offs\nNone.\n")


def _repo(settings: dict, design_min_stories: int = 1, stories: int = 1):
    """A repo with an approved single-story change; design_min_stories=1 makes the architect run for it."""
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    repository(root)
    (root / "modules/orders").mkdir(parents=True)
    (root / "modules/__init__.py").write_text("", encoding="utf-8")
    (root / "modules/orders/__init__.py").write_text("", encoding="utf-8")
    (root / "modules/orders/service.py").write_text("VALUE = 0\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests/__init__.py").write_text("", encoding="utf-8")
    (root / "tests/test_existing.py").write_text(
        "import unittest\n\n\nclass Existing(unittest.TestCase):\n    def test_truth(self):\n"
        "        self.assertTrue(True)\n", encoding="utf-8")
    git(root, "add", "."); git(root, "commit", "-q", "-m", "existing")
    bootstrap_project(root, settings)
    config = load_json(root / ".storyloop/config.json")
    config["design_min_stories"] = design_min_stories
    write_json(root / ".storyloop/config.json", config)
    state = create_change(root, "orders", _product_with(stories), now=lambda: NOW)
    approve_change(root, state["change_id"], now=lambda: NOW)
    return temporary, root, state["change_id"]


def _product_with(stories: int) -> dict:
    value = product()
    first = value["stories"][0]
    for n in range(2, stories + 1):
        story = json.loads(json.dumps(first))
        story["id"] = f"ST-{n}"; story["title"] = f"{first['title']} {n}"
        for criterion in story["acceptance"]:
            criterion["id"] = f"{criterion['id']}-{n}"
        value["stories"].append(story)
    return value


def _implement(project, context):
    (project / "modules/orders/service.py").write_text("VALUE = 1\n", encoding="utf-8")
    return {"summary": "done", "questions": [], "design": "## Tests First\nnone needed\n"}


def _services(change_id, design, implement=_implement, characterize=None, seen=None, criteria=("AC-1",)):
    seen = seen if seen is not None else {}

    def smoke(project, state):
        payload = smoke_payload(change_id)
        payload["manifest"]["tests"][0]["criteria"] = list(criteria)
        return payload

    def remember(name, fn):
        def wrapped(project, context):
            seen.setdefault(name, []).append(context)
            return fn(project, context)
        return wrapped

    return LoopServices(
        generate_smoke=smoke,
        implement=remember("implement", implement),
        review=remember("review", lambda project, context: {"verdict": "pass", "summary": "ok", "findings": [],
                                                             "questions": []}),
        characterize=remember("characterize", characterize) if characterize else None,
        design=remember("design", design) if design else None,
    )


class DesignRoleTests(unittest.TestCase):
    def test_design_runs_once_after_red_and_feeds_every_later_role(self):
        temporary, root, change_id = _repo({"unit_commands": [UNIT], "test_roots": ["tests"]})
        seen: dict = {}

        def design(project, context):
            self.assertIn("change", context)
            self.assertNotIn("characterization", context)
            return {"summary": "plan", "touched": ["modules/orders/service.py"], "design": PLAN, "questions": []}

        def characterize(project, context):
            self.assertEqual(context["design"]["touched"], ["modules/orders/service.py"])
            (project / "tests/test_lock.py").write_text(
                "import unittest\nfrom modules.orders import service\n\n\nclass Lock(unittest.TestCase):\n"
                "    def test_value(self):\n        self.assertIn('VALUE', dir(service))\n", encoding="utf-8")
            return {"summary": "locked", "touched": [], "tests": ["tests/test_lock.py"], "questions": []}

        try:
            result = run_change(root, change_id, _services(change_id, design, characterize=characterize, seen=seen))
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(len(seen["design"]), 1)
            self.assertEqual(seen["implement"][0]["design_plan"]["touched"], ["modules/orders/service.py"])
            self.assertIn("## Decisions", seen["implement"][0]["design_plan"]["design"])
            self.assertIn("design_plan", seen["review"][0])
            state = load_run_state(root, change_id)
            self.assertEqual(state["design_plan"]["status"], "DONE")
            self.assertEqual(state["characterization"]["touched"], ["modules/orders/service.py"])
            design_md = (root / "openspec/changes" / change_id / "design.md").read_text(encoding="utf-8")
            self.assertIn("## Decisions", design_md)
            self.assertIn("## Tests First", design_md)  # the implementer's record follows the plan
        finally:
            temporary.cleanup()

    def test_design_questions_pause_the_run_and_answers_let_it_continue(self):
        temporary, root, change_id = _repo({"unit_commands": [UNIT]})
        calls: list = []

        def design(project, context):
            calls.append(context)
            if not context.get("answers"):
                return {"summary": "need a decision", "touched": [], "design": "",
                        "questions": ["Reuse the dictionary service or add a table?"]}
            return {"summary": "plan", "touched": ["modules/orders/service.py"], "design": PLAN, "questions": []}

        try:
            paused = run_change(root, change_id, _services(change_id, design))
            self.assertEqual(paused["status"], "NEEDS_INPUT")
            self.assertEqual(paused["questions"], ["Reuse the dictionary service or add a table?"])
            record_answers(root, change_id, ["Reuse the dictionary service"], now=lambda: NOW)
            result = run_change(root, change_id, _services(change_id, design))
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1]["answers"][0]["answer"], "Reuse the dictionary service")
        finally:
            temporary.cleanup()

    def test_design_must_name_existing_files_and_may_not_write(self):
        temporary, root, change_id = _repo({"unit_commands": [UNIT]})
        try:
            with self.assertRaisesRegex(StoryLoopError, "exist"):
                run_change(root, change_id, _services(
                    change_id, lambda p, c: {"summary": "x", "touched": ["modules/orders/missing.py"],
                                             "design": PLAN, "questions": []}))
        finally:
            temporary.cleanup()
        temporary, root, change_id = _repo({"unit_commands": [UNIT]})
        try:
            def writes(project, context):
                (project / "modules/orders/service.py").write_text("VALUE = 9\n", encoding="utf-8")
                return {"summary": "x", "touched": [], "design": PLAN, "questions": []}
            with self.assertRaises(StoryLoopError) as caught:
                run_change(root, change_id, _services(change_id, writes))
            self.assertEqual(caught.exception.code, "WORKSPACE_TAINTED")
        finally:
            temporary.cleanup()

    def test_without_a_design_service_the_step_is_skipped(self):
        temporary, root, change_id = _repo({"unit_commands": [UNIT]})
        seen: dict = {}
        try:
            result = run_change(root, change_id, _services(change_id, None, seen=seen))
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(load_run_state(root, change_id)["design_plan"]["status"], "SKIPPED")
            self.assertIsNone(seen["implement"][0]["design_plan"])
        finally:
            temporary.cleanup()


class DesignSkipPolicyTests(unittest.TestCase):
    """Small changes skip the architect: by default a single-story change is small."""

    def _design(self):
        return lambda project, context: {"summary": "plan", "touched": ["modules/orders/service.py"],
                                         "design": PLAN, "questions": []}

    def test_a_single_story_change_skips_design_by_default(self):
        temporary, root, change_id = _repo({"unit_commands": [UNIT]}, design_min_stories=2)
        seen: dict = {}
        try:
            result = run_change(root, change_id, _services(change_id, self._design(), seen=seen))
            self.assertEqual(result["status"], "PASSED")
            self.assertNotIn("design", seen)
            plan = load_run_state(root, change_id)["design_plan"]
            self.assertEqual(plan["status"], "SKIPPED")
            self.assertIn("1 story", plan["reason"])
            self.assertIsNone(seen["implement"][0]["design_plan"])
        finally:
            temporary.cleanup()

    def test_two_stories_run_design_by_default(self):
        temporary, root, change_id = _repo({"unit_commands": [UNIT]}, design_min_stories=2, stories=2)
        seen: dict = {}
        try:
            result = run_change(root, change_id, _services(change_id, self._design(), seen=seen,
                                                           criteria=("AC-1", "AC-1-2")))
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(len(seen["design"]), 1)
            self.assertEqual(load_run_state(root, change_id)["design_plan"]["status"], "DONE")
        finally:
            temporary.cleanup()

    def test_the_threshold_is_configurable_and_a_fresh_project_defaults_to_two(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root, {"unit_commands": []})
            self.assertEqual(load_json(root / ".storyloop/config.json")["design_min_stories"], 2)
        temporary, root, change_id = _repo({"unit_commands": [UNIT]}, design_min_stories=1)
        seen: dict = {}
        try:
            run_change(root, change_id, _services(change_id, self._design(), seen=seen))
            self.assertEqual(len(seen["design"]), 1)
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
