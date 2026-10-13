"""Inputs that shape the oracle (skill packs) cannot be made writable for the implementer."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from storyloop.changes import create_change
from storyloop.models import StoryLoopError
from storyloop.project_loop import LoopServices, run_change
from storyloop.project_profile import validate_project_settings
from tests.support import product
from tests.test_baseline_behavior import git, repository
from tests.test_project_loop import smoke_payload


def review(project, context):
    return {"verdict": "pass", "summary": "ok", "findings": [], "questions": []}


class SkillDirectoryTests(unittest.TestCase):
    def test_skill_directories_cannot_be_module_or_test_roots(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            for settings in ({"module_roots": [".claude"]}, {"module_roots": [".codex/skills/x"]},
                             {"test_roots": ["**"]}, {"module_roots": ["."]}):
                with self.subTest(settings=settings), self.assertRaises(StoryLoopError):
                    validate_project_settings(root, settings)
            validate_project_settings(root, {"module_roots": ["modules"], "test_roots": ["tests/**"]})

    def test_an_implementer_editing_a_skill_pack_stops_at_the_base_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / ".claude/skills/oracle-craft").mkdir(parents=True)
            (root / ".claude/skills/oracle-craft/SKILL.md").write_text("rules\n", encoding="utf-8")
            git(root, "add", "."); git(root, "commit", "-q", "-m", "skills")
            bootstrap_project(root, {"test_commands": []})
            change_id = create_change(root, "orders", product())["change_id"]
            approve_change(root, change_id)

            def implement(project, context):
                (project / "modules/orders").mkdir(parents=True, exist_ok=True)
                (project / "modules/orders/service.py").write_text("VALUE = 1\n", encoding="utf-8")
                (project / ".claude/skills/oracle-craft/SKILL.md").write_text("weaker\n", encoding="utf-8")
                return {"summary": "done", "questions": []}

            result = run_change(root, change_id, LoopServices(
                generate_smoke=lambda project, state: smoke_payload(change_id), implement=implement, review=review))
            self.assertEqual(result["status"], "BASE_CHANGE_REQUIRED")
            self.assertIn(".claude/skills/oracle-craft/SKILL.md", [v["path"] for v in result["violations"]])


if __name__ == "__main__":
    unittest.main()
