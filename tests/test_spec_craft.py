"""The spec-craft package is available to agents without changing engine roles."""

from pathlib import Path
import re
import sys
import tempfile
import unittest

from storyloop.baseline import bootstrap_project
from storyloop.skills import (
    MAX_SKILL_BYTES, SKILLS_ROOT, SKILL_TARGETS, _frontmatter,
    install_skills, packaged_skills, select_skills, skill_text,
)
from tests.test_baseline_behavior import git, repository


SKILL = Path(SKILLS_ROOT) / "spec-craft"
REFERENCES = (
    "assess.md", "requirement-template.md", "spec-template.md", "ears.md",
    "examples.md", "clarify.md", "handoff.md",
)


def read(name: str) -> str:
    return (SKILL / name).read_text(encoding="utf-8")


def section(text: str, title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if match is None:
        raise AssertionError(f"Missing section: {title}")
    return match.group(1)


class SpecCraftPackageTests(unittest.TestCase):
    def test_spec_craft_is_packaged_with_exactly_its_references(self):
        skill = packaged_skills()["spec-craft"]
        self.assertEqual(skill["version"], "1.0.0")
        self.assertEqual(skill["files"], sorted(["SKILL.md", *(f"references/{n}" for n in REFERENCES)]))

    def test_frontmatter_has_only_name_version_description(self):
        meta = _frontmatter(read("SKILL.md"))
        self.assertEqual(set(meta), {"name", "version", "description"})
        self.assertEqual(meta["name"], "spec-craft")
        self.assertIn("需求", meta["description"])
        self.assertIn("spec", meta["description"].lower())

    def test_spec_craft_reaches_no_engine_role(self):
        with tempfile.TemporaryDirectory() as folder:
            for role in ("product", "acceptance", "implement", "review", "design", "characterize", "survey"):
                with self.subTest(role=role):
                    self.assertNotIn("spec-craft", select_skills(Path(folder), role, []))

    def test_skill_md_links_every_reference_and_nothing_else(self):
        linked = set(re.findall(r"references/([a-z-]+\.md)", read("SKILL.md")))
        self.assertEqual(linked, set(REFERENCES))

    def test_spec_craft_fits_the_delivery_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertLessEqual(len(skill_text(Path(folder), "spec-craft").encode("utf-8")), MAX_SKILL_BYTES // 2)

    def test_install_writes_both_agent_copies(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            install_skills(root)
            for target in SKILL_TARGETS:
                self.assertTrue((root / target / "spec-craft/SKILL.md").is_file())
                self.assertTrue((root / target / "spec-craft/references/clarify.md").is_file())

    def test_install_on_initialized_project_proposes_spec_craft_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / "README.md").write_text("# demo\n", encoding="utf-8")
            git(root, "add", "README.md")
            git(root, "commit", "-q", "-m", "readme")
            bootstrap_project(root, {"test_commands": [[sys.executable, "-c", "pass"]]})
            result = install_skills(root)
            self.assertIn("proposal", result)
            self.assertTrue(any("spec-craft/" in path for path in result["written"]))


ASSESS_QUESTIONS = ("谁遇到了什么问题", "凭什么知道问题解决了", "不做会怎样")
REQUIREMENT_SECTIONS = ("目标", "非目标", "用户场景", "成功标准", "业务规则", "边界情况",
                        "关键实体与术语", "假设", "澄清记录", "所属模块")
LITE_SECTIONS = ("目标", "非目标", "用户场景", "业务规则")


class AssessAndRequirementTests(unittest.TestCase):
    def test_assess_has_the_three_questions_and_three_outcomes(self):
        text = read("references/assess.md")
        for phrase in (*ASSESS_QUESTIONS, "做", "需澄清", "放弃", "由人决定"):
            self.assertIn(phrase, text)

    def test_assess_says_abandoning_is_a_success(self):
        self.assertIn("放弃也算成功", read("references/assess.md"))

    def test_full_template_has_every_section_in_order(self):
        headings = re.findall(r"^### (.+)$", section(read("references/requirement-template.md"), "完整版"), re.M)
        self.assertEqual([h.split("（")[0] for h in headings], list(REQUIREMENT_SECTIONS))

    def test_lite_template_has_only_the_starred_sections(self):
        headings = re.findall(r"^### (.+)$", section(read("references/requirement-template.md"), "轻量版"), re.M)
        self.assertEqual([h.split("（")[0] for h in headings], list(LITE_SECTIONS))

    def test_requirement_template_forbids_technical_words_and_needs_a_named_person(self):
        text = read("references/requirement-template.md")
        for phrase in ("不出现技术词", "具体人物", "可度量"):
            self.assertIn(phrase, text)
