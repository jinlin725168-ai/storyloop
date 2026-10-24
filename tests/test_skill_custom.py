"""Team and personal spec-craft customization contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from storyloop.models import StoryLoopError
from storyloop.skill_custom import custom_paths, load_customization, render_view, write_view
from storyloop.skills import skill_text


class _Workspace(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.team, self.user = custom_paths(self.root, "spec-craft")
        (self.root / "docs").mkdir()
        (self.root / "docs/a.md").write_text("a", encoding="utf-8")
        (self.root / "docs/b.md").write_text("b", encoding="utf-8")

    def write(self, path: Path, content: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


class CustomizationTests(_Workspace):
    def test_no_files_means_empty_customization(self):
        value = load_customization(self.root, "spec-craft")
        self.assertEqual(value, {
            "version": 1, "preferences": [], "glossary": None,
            "coverage": [], "sections": [], "sources": [],
        })

    def test_user_file_overrides_scalar_and_appends_plain_array(self):
        self.write(self.team, 'version = 1\nglossary = "docs/a.md"\npreferences = ["先问范围"]\n')
        self.write(self.user, 'version = 1\nglossary = "docs/b.md"\npreferences = ["少用选择题"]\n')
        value = load_customization(self.root, "spec-craft")
        self.assertEqual(value["glossary"], "docs/b.md")
        self.assertEqual(value["preferences"], ["先问范围", "少用选择题"])

    def test_coverage_and_sections_replace_matching_codes_in_place(self):
        self.write(self.team, 'version = 1\n'
                   '[[coverage]]\ncode = "pay"\nname = "支付"\ncheck = "退款时限"\n'
                   '[[coverage]]\ncode = "audit"\nname = "审计"\ncheck = "日志留存"\n'
                   '[[sections]]\ncode = "source"\ndocument = "spec"\n'
                   'title = "来源"\nguidance = "记录来源"\n')
        self.write(self.user, 'version = 1\n'
                   '[[coverage]]\ncode = "pay"\nname = "支付"\ncheck = "退款与拒付时限"\n'
                   '[[sections]]\ncode = "source"\ndocument = "requirement"\n'
                   'title = "来源"\nguidance = "记录用户来源"\n')
        value = load_customization(self.root, "spec-craft")
        self.assertEqual([(row["code"], row["check"]) for row in value["coverage"]],
                         [("pay", "退款与拒付时限"), ("audit", "日志留存")])
        self.assertEqual(value["sections"][0]["guidance"], "记录用户来源")

    def test_unknown_keys_are_rejected_by_name(self):
        self.write(self.team, 'version = 1\nhuman_decisions = []\n')
        with self.assertRaisesRegex(StoryLoopError, "human_decisions"):
            load_customization(self.root, "spec-craft")

    def test_invalid_entry_key_and_code_are_rejected(self):
        for content, key in (
            ('version = 1\n[[coverage]]\ncode = "pay"\nname = "支付"\ncheck = "退款"\nremove = true\n', "remove"),
            ('version = 1\n[[sections]]\ncode = "BAD"\ndocument = "spec"\ntitle = "标题"\nguidance = "指引"\n', "code"),
        ):
            with self.subTest(key=key):
                self.write(self.team, content)
                with self.assertRaisesRegex(StoryLoopError, key):
                    load_customization(self.root, "spec-craft")

    def test_glossary_must_be_a_safe_repository_path(self):
        for bad in ("../outside.md", "/etc/passwd"):
            with self.subTest(path=bad):
                self.write(self.team, f'version = 1\nglossary = "{bad}"\n')
                with self.assertRaisesRegex(StoryLoopError, "glossary"):
                    load_customization(self.root, "spec-craft")
        (self.root / "docs/link.md").symlink_to(self.root / "docs/a.md")
        self.write(self.team, 'version = 1\nglossary = "docs/link.md"\n')
        with self.assertRaisesRegex(StoryLoopError, "glossary"):
            load_customization(self.root, "spec-craft")

    def test_invalid_toml_and_utf8_name_the_file_and_line(self):
        self.write(self.team, 'version = 1\npreferences = [\n')
        with self.assertRaisesRegex(StoryLoopError, r"spec-craft\.toml.*2"):
            load_customization(self.root, "spec-craft")
        self.team.write_bytes(b"version = 1\npreferences = \xff\n")
        with self.assertRaisesRegex(StoryLoopError, r"spec-craft\.toml.*2"):
            load_customization(self.root, "spec-craft")

    def test_only_registered_skills_are_customizable(self):
        with self.assertRaises(StoryLoopError):
            load_customization(self.root, "oracle-craft")

    def test_sources_record_each_file_digest_in_merge_order(self):
        self.write(self.team, "version = 1\n")
        self.write(self.user, "version = 1\n")
        sources = load_customization(self.root, "spec-craft")["sources"]
        self.assertEqual([item["path"] for item in sources],
                         ["storyloop-custom/spec-craft.toml", "storyloop-custom/spec-craft.user.toml"])
        self.assertEqual([item["sha256"] for item in sources], [
            hashlib.sha256(path.read_bytes()).hexdigest() for path in (self.team, self.user)
        ])


class ViewTests(_Workspace):
    def test_view_without_customization_equals_the_skill_text(self):
        self.assertEqual(render_view(self.root, "spec-craft"), skill_text(self.root, "spec-craft"))

    def test_view_appends_coverage_sections_preferences_and_glossary(self):
        self.write(self.team, 'version = 1\npreferences = ["先问范围"]\nglossary = "docs/a.md"\n'
                   '[[coverage]]\ncode = "pay"\nname = "支付合规"\ncheck = "退款时限"\n'
                   '[[sections]]\ncode = "source"\ndocument = "requirement"\n'
                   'title = "来源"\nguidance = "记录用户来源"\n')
        view = render_view(self.root, "spec-craft")
        self.assertTrue(view.startswith(skill_text(self.root, "spec-craft")))
        for phrase in (
            "## 项目定制（由 storyloop skills --view 生成，请勿手工编辑）",
            "| 11 | 支付合规 | 退款时限 |", "#### 需求文档：来源", "记录用户来源",
            "- 先问范围", "docs/a.md", "storyloop-custom/spec-craft.toml",
        ):
            self.assertIn(phrase, view)

    def test_view_is_deterministic(self):
        self.write(self.team, 'version = 1\npreferences = ["先问范围"]\n')
        self.assertEqual(render_view(self.root, "spec-craft"), render_view(self.root, "spec-craft"))

    def test_write_view_reports_digest_and_does_not_install_skills(self):
        self.write(self.team, 'version = 1\npreferences = ["先问范围"]\n')
        result = write_view(self.root, "spec-craft")
        text = (self.root / result["path"]).read_text(encoding="utf-8")
        self.assertEqual(result["sha256"], hashlib.sha256(text.encode("utf-8")).hexdigest())
        self.assertEqual(result["status"], "SKILL_VIEW")
        self.assertEqual(result["path"], ".storyloop/skills-view/spec-craft.md")
        self.assertTrue(result["customized"])
        self.assertFalse((self.root / ".claude/skills/spec-craft").exists())

    def test_cli_view_prints_the_result(self):
        result = subprocess.run(
            [sys.executable, "-m", "storyloop", "skills", str(self.root), "--view", "spec-craft", "--json"],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "SKILL_VIEW")
