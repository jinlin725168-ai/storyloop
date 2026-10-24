"""The spec-craft package is available to agents without changing engine roles."""

from pathlib import Path
import re
import sys
import tempfile
import unittest

from storyloop.baseline import bootstrap_project
from storyloop import skill_custom
from storyloop.skill_custom import DEFAULT_QUESTION_LIMIT
from storyloop.state_format import ROW_TYPES
from storyloop.skills import (
    MAX_SKILL_BYTES, SKILLS_ROOT, SKILL_TARGETS, _frontmatter,
    install_skills, packaged_skills, select_skills, skill_text,
)
from tests.test_baseline_behavior import git, repository


SKILL = Path(SKILLS_ROOT) / "spec-craft"
ROOT = SKILL.parent.parent.parent
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
        self.assertEqual(skill["version"], "1.4.0")
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

    def test_lite_and_full_have_a_renderable_assumptions_table(self):
        template = read("references/requirement-template.md")
        lite = section(template, "轻量版")
        full = section(template, "完整版")
        for body in (lite, full):
            self.assertIn("| 编号 | 假设 | 来源 | 状态 |", body)
            self.assertIn("|---|---|---|---|", body)
            self.assertRegex(body, r"\| A1 \|[^\n]*10 秒[^\n]*\| 待确认 \|")
            self.assertRegex(body, r"\| A1 \|[^\n]*\| 产品负责人：评估第 2 问 \| 待确认 \|")
            self.assertNotRegex(body, r"\| A1 \|[^\n]*\| 试用操作者：")

    def test_requirement_template_forbids_technical_words_and_needs_a_named_person(self):
        text = read("references/requirement-template.md")
        for phrase in ("不出现技术词", "具体人物", "可度量"):
            self.assertIn(phrase, text)


EARS_PATTERNS = {"普遍": "THE SYSTEM SHALL", "事件驱动": "WHEN", "状态驱动": "WHILE",
                 "不期望行为": "IF", "可选特性": "WHERE"}


class SpecTemplateTests(unittest.TestCase):
    def test_ears_covers_all_five_patterns_with_keywords(self):
        text = read("references/ears.md")
        for name, keyword in EARS_PATTERNS.items():
            with self.subTest(pattern=name):
                self.assertIn(f"## {name}", text)
                self.assertIn(keyword, section(text, name))

    def test_unwanted_behaviour_pattern_uses_if_then(self):
        self.assertIn("THEN", section(read("references/ears.md"), "不期望行为"))

    def test_examples_require_a_positive_and_a_counter_example_per_rule(self):
        text = read("references/examples.md")
        for phrase in ("至少一个正例", "至少一个反例", "具体数据"):
            self.assertIn(phrase, text)

    def test_examples_point_to_oracle_craft_row_types_instead_of_copying_them(self):
        text = read("references/examples.md")
        self.assertIn("oracle-craft", text)
        self.assertIn("acceptance-matrix.md", text)
        self.assertLess(sum(row in text for row in ROW_TYPES), len(ROW_TYPES))

    def test_counterexample_kind_is_independent_of_old_code_row_type(self):
        text = read("references/examples.md")
        self.assertNotIn("反例通常是", text)
        for phrase in ("正反：正例", "正反：反例", "旧代码", "degradation", "regression"):
            self.assertIn(phrase, text)

    def test_completed_task_boundary_example_is_regression(self):
        text = read("references/spec-template.md")
        self.assertIn("正反：反例；行类型见验收矩阵的 `regression`", text)

    def test_spec_template_is_organized_per_rule(self):
        text = read("references/spec-template.md")
        for phrase in ("规则", "EARS", "正例", "反例", "Given", "When", "Then", "外部可观察"):
            self.assertIn(phrase, text)
        self.assertIn("不写类名", text)


COVERAGE = ("功能范围与行为", "领域与数据", "交互流程", "非功能", "外部依赖",
            "边界与失败", "约束与取舍", "术语一致性", "完成标准", "占位与含糊词")


class ClarifyAndHandoffTests(unittest.TestCase):
    def test_coverage_map_has_ten_categories_and_three_states(self):
        text = read("references/clarify.md")
        rows = re.findall(r"^\| *(\d+) *\| *([^|]+?) *\|", text, re.M)
        self.assertEqual([name for _, name in rows][:10], list(COVERAGE))
        for state in ("清楚", "部分", "缺失"):
            self.assertIn(state, text)

    def test_question_limits_are_exact(self):
        text = read("references/clarify.md")
        for phrase in ("最多 3 处", "生效上限", "一次只问一个"):
            self.assertIn(phrase, text)
        self.assertIn(f"每步默认最多 {DEFAULT_QUESTION_LIMIT} 个问题", text)
        self.assertNotIn("跨阶段", text)

    def test_question_priority_and_format(self):
        text = read("references/clarify.md")
        self.assertIn("范围 > 安全与隐私 > 体验 > 技术细节", text)
        for phrase in ("为什么重要", "推荐", "选项"):
            self.assertIn(phrase, text)

    def test_answers_are_written_back_without_contradictions(self):
        self.assertIn("不能留下互相矛盾", read("references/clarify.md"))

    def test_handoff_names_the_draft_folder_and_the_command(self):
        text = read("references/handoff.md")
        for phrase in (".storyloop/drafts/spec/", "requirements.md", "spec.md", "storyloop change --idea"):
            self.assertIn(phrase, text)

    def test_handoff_keeps_approval_in_storyloop(self):
        text = read("references/handoff.md")
        self.assertIn("批准", text)
        self.assertIn("storyloop approve --change", text)


class DocumentationTests(unittest.TestCase):
    def test_roles_explains_chat_only_usage_and_installation(self):
        text = (ROOT / "docs/roles.md").read_text(encoding="utf-8")
        for phrase in ("spec-craft", "Claude Code", "Codex", "storyloop skills", "不下发给任何引擎角色"):
            self.assertIn(phrase, text)

    def test_testing_map_lists_spec_craft(self):
        text = (ROOT / "docs/testing.md").read_text(encoding="utf-8")
        line = next(line for line in text.splitlines() if "命令面与技能" in line)
        self.assertIn("test_spec_craft", line)

    def test_trial_record_exists(self):
        self.assertTrue((ROOT / "docs/validation/2026-10-24-spec-craft-第1批试用.md").is_file())

    def test_batch2_docs_explain_the_view_and_customization(self):
        api = (ROOT / "docs/api.md").read_text(encoding="utf-8")
        roles = (ROOT / "docs/roles.md").read_text(encoding="utf-8")
        for phrase in ("--view", "SKILL_VIEW", "storyloop-custom/spec-craft.toml",
                       "storyloop-custom/spec-craft.user.toml", ".git/info/exclude"):
            self.assertIn(phrase, api)
        for phrase in ("--view spec-craft", ".storyloop/skills-view/spec-craft.md"):
            self.assertIn(phrase, roles)

    def test_batch2_trial_record_exists(self):
        self.assertTrue((ROOT / "docs/validation/2026-10-24-spec-craft-第2批试用.md").is_file())


class Batch2ContentTests(unittest.TestCase):
    def test_skill_md_tells_the_agent_to_load_the_project_view(self):
        text = read("SKILL.md")
        for phrase in ("storyloop skills", "--view spec-craft", "storyloop-custom/"):
            self.assertIn(phrase, text)

    def test_examples_define_positive_and_negative_by_premise(self):
        text = read("references/examples.md")
        for phrase in ("前提成立", "前提不成立", "不误触发", "在旧代码上会不会失败"):
            self.assertIn(phrase, text)
        self.assertNotIn("待定", text)

    def test_superseded_statements_live_only_in_the_clarification_log(self):
        for name in ("references/clarify.md", "references/requirement-template.md"):
            with self.subTest(file=name):
                self.assertIn("原说法 → 新说法", read(name))

    def test_every_assumption_goes_into_the_table(self):
        text = read("references/requirement-template.md")
        self.assertIn("所有假设都写进", text)
        self.assertIn("不得在表外", text)

    def test_spec_template_example_uses_the_new_definitions(self):
        text = read("references/spec-template.md")
        self.assertIn("前提不成立", text)
        self.assertIn("regression", text)


class Trial2FixTests(unittest.TestCase):
    def test_invalid_date_example_follows_oracle_matrix(self):
        text = read("references/examples.md")
        self.assertIn("无效日期", text)
        self.assertRegex(text, r"无效日期[^\n]*选 `positive`")
        self.assertRegex(text, r"`degradation`[^\n]*依赖失败")
        self.assertNotRegex(text, r"无效日期[^\n]*选 `degradation`")

    def test_assumptions_include_human_expectations_and_unambiguous_statuses(self):
        template = read("references/requirement-template.md")
        assess = read("references/assess.md")
        for phrase in ("人或模型", "10 秒", "来源", "待确认", "已确认", "试用操作者"):
            self.assertIn(phrase, template)
        self.assertIn("假设表", assess)
        self.assertIn("轻量版", assess)
        self.assertIn("预期", assess)
        self.assertNotIn("试用中暂采纳", template)
        self.assertNotIn("负责人已确认", template)

    def test_question_limit_covers_every_conversation_step_with_default_fallback(self):
        skill = read("SKILL.md")
        clarify = read("references/clarify.md")
        design = (ROOT / "docs/superpowers/specs/2026-10-23-需求共创技能-design.md").read_text(
            encoding="utf-8")
        for name in ("评估", "需求", "规格", "澄清", "交接"):
            self.assertIn(name, skill)
        self.assertIn("步骤 1 评估、2 需求、3 规格、4 澄清、5 交接", skill)
        for text in (skill, clarify):
            for phrase in ("分别计数", "不跨步骤累加", "生效上限"):
                self.assertIn(phrase, text)
            self.assertIn(f"每步默认最多 {DEFAULT_QUESTION_LIMIT} 个问题", text)
            self.assertNotIn("跨阶段", text)
        self.assertNotIn("规格核对", skill)
        self.assertIn("question_limit", design)

    def test_customization_docs_explain_question_limit(self):
        api = (ROOT / "docs/api.md").read_text(encoding="utf-8")
        roles = (ROOT / "docs/roles.md").read_text(encoding="utf-8")
        for phrase in ("question_limit", "每步骤", "标量", "question_limit_source"):
            self.assertIn(phrase, api)
        self.assertIn("question_limit", roles)
        design = (ROOT / "docs/superpowers/specs/2026-10-23-需求共创技能-design.md").read_text(
            encoding="utf-8")
        self.assertIn(f"默认 {DEFAULT_QUESTION_LIMIT}", api)
        self.assertIn(f"默认 {DEFAULT_QUESTION_LIMIT}", design)
        minimum = getattr(skill_custom, "MIN_QUESTION_LIMIT", None)
        self.assertIsInstance(minimum, int)
        self.assertIn(f"{minimum}–{skill_custom.MAX_QUESTION_LIMIT}", api)
        self.assertIn(f"{minimum}–{skill_custom.MAX_QUESTION_LIMIT}", design)
        self.assertNotIn("跨阶段", design)
