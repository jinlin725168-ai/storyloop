"""Packaged skills install into any project for Claude Code and Codex, and the survey role runs the skill."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

from storyloop.baseline import bootstrap_project
from storyloop.models import StoryLoopError
from storyloop.project_agents import instructions_for, invoke_project_agent
from storyloop.skills import (SKILL_TARGETS, install_skills, packaged_skills, skill_status, skill_text)
from storyloop.storage import load_json, write_json
from tests.test_baseline_behavior import git, repository


class PackagedSkillTests(unittest.TestCase):
    def test_foundation_survey_skill_is_packaged_with_its_references(self):
        skills = packaged_skills()
        self.assertIn("foundation-survey", skills)
        skill = skills["foundation-survey"]
        self.assertEqual(skill["version"], "1.0.0")
        self.assertTrue(skill["description"])
        names = {Path(p).name for p in skill["files"]}
        self.assertEqual(names, {"SKILL.md", "doc-templates.md", "survey-checklist.md"})

    def test_install_writes_claude_and_codex_copies_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            first = install_skills(root)
            self.assertEqual(first["status"], "SKILLS_INSTALLED")
            for target in SKILL_TARGETS:
                self.assertTrue((root / target / "foundation-survey/SKILL.md").is_file())
                self.assertTrue((root / target / "foundation-survey/references/doc-templates.md").is_file())
            self.assertEqual(sorted(first["written"]), sorted(first["written"]))
            self.assertTrue(first["written"])
            second = install_skills(root)
            self.assertEqual(second["written"], [])
            status = skill_status(root)
            self.assertEqual(status["foundation-survey"]["packaged"], "1.0.0")
            self.assertEqual(status["foundation-survey"]["installed"], {t: "1.0.0" for t in SKILL_TARGETS})

    def test_install_on_an_initialized_project_opens_a_base_proposal(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / "README.md").write_text("# demo\n", encoding="utf-8")
            git(root, "add", "README.md")
            git(root, "commit", "-q", "-m", "readme")
            bootstrap_project(root, {"test_commands": [[sys.executable, "-c", "pass"]]})
            result = install_skills(root)
            proposal = load_json(Path(result["proposal"]))
            self.assertEqual({item["path"] for item in proposal["changes"]}, set(result["written"]))
            self.assertIn("base-update", result["next"])


class SurveyUsesSkillTests(unittest.TestCase):
    def test_survey_instructions_embed_the_skill_and_prefer_the_project_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            packaged = instructions_for(root, "survey")
            self.assertIn("Foundation Survey", packaged)
            self.assertIn("doc-templates", packaged)
            self.assertIn('{"documents"', packaged.replace(" ", ""))
            install_skills(root)
            local = root / SKILL_TARGETS[0] / "foundation-survey/SKILL.md"
            local.write_text(local.read_text(encoding="utf-8") + "\nPROJECT-LOCAL-MARKER\n", encoding="utf-8")
            self.assertIn("PROJECT-LOCAL-MARKER", instructions_for(root, "survey"))
            self.assertIn("PROJECT-LOCAL-MARKER", skill_text(root, "foundation-survey"))

    def test_manual_survey_request_carries_the_skill_instructions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / "README.md").write_text("# demo\n", encoding="utf-8")
            git(root, "add", "README.md")
            git(root, "commit", "-q", "-m", "readme")
            bootstrap_project(root, {"test_commands": [[sys.executable, "-c", "pass"]]})
            config = load_json(root / ".storyloop/config.json")
            config["adapter"] = "manual"
            write_json(root / ".storyloop/config.json", config)
            with self.assertRaises(StoryLoopError):
                invoke_project_agent(root, "survey", {"baseline_version": "1.0.0"})
            request = load_json(root / ".storyloop/requests/project-survey.json")
            self.assertIn("Foundation Survey", request["instructions"])


if __name__ == "__main__":
    unittest.main()
