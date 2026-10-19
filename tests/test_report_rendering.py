import json
from pathlib import Path
import unittest

from storyloop.change_report import merge_receipt_bytes
from storyloop.report_markdown import render_presentation


FIXTURES = Path(__file__).parent / "fixtures"


def _item(identifier, data=None, source="machine", availability="AVAILABLE", reason=None):
    result = {"id": identifier, "source": source, "availability": availability, "data": data}
    if reason:
        result["reason"] = reason
    return result


def _report(tier="standard"):
    sections = {str(index): {"title": title, "items": []} for index, title in enumerate(
        ("头部", "结论", "审查重点", "证据", "过程", "成本", "记忆"))}
    sections["0"]["items"] = [
        _item("identity", {"change_id": "C|<1>", "status": "PASSED"}),
        _item("implementation_commit", "b" * 40),
        _item("merge_commit", None, availability="PENDING", reason="SOURCE_NOT_PERSISTED"),
    ]
    sections["1"]["items"] = [
        _item("goal", {"value": "<script>alert(1)</script> **claim**", "author": "NOT_AVAILABLE",
                       "provenance": "NOT_AVAILABLE", "verification": "CONTENT_NOT_MACHINE_VERIFIED"},
              reason="STATEMENT_PROVENANCE_NOT_PERSISTED"),
        _item("completion", {"completed": 1, "total": 2}),
    ]
    sections["2"]["items"] = [_item("verification", [
        {"criterion_id": "AC-1", "test_id": "T-1", "command": "python 'a b.py'"}])]
    sections["3"]["items"] = [
        _item("layers", {"unit": {"status": "PASSED", "results": []}}),
        _item("baseline", {"unit": [1]}),
        _item("executed", {"unit": [{"id": "T-1", "executed": 1, "parser": "unittest"}]}),
        _item("ci", None, availability="NOT_AVAILABLE", reason="NO_AUTHORITATIVE_EXTERNAL_CI_RECORD"),
    ]
    sections["4"]["items"] = [_item("answers", [{"question": "x|y", "answer": "<b>no</b>"}], "human")]
    sections["5"]["items"] = [_item("cost", {"calls": 2, "failed": 1, "cost_usd": None,
                                                "known_cost_usd": 0.25,
                                                "coverage": {"cost_usd": {"known": 1, "total": 2}}})]
    sections["6"]["items"] = [_item("memory", None, availability="NOT_AVAILABLE",
                                             reason="MEMORY_MODULE_NOT_IMPLEMENTED")]
    return {"change_id": "C1", "status": "PASSED", "merge_commit": None,
            "presentation": {"version": 1, "tier": tier, "sections": sections,
                             "reconciliation": {"status": "MATCH", "checks": [], "limitations": []}}}


class ReportRenderingTests(unittest.TestCase):
    def test_light_renders_only_reader_sections_zero_one_and_three(self):
        markdown = render_presentation(_report("light"))
        self.assertIn("## 0. 头部", markdown)
        self.assertIn("## 1. 结论", markdown)
        self.assertIn("## 3. 证据", markdown)
        for number in (2, 4, 5, 6):
            self.assertNotIn(f"## {number}.", markdown)

    def test_standard_and_heavy_render_all_sections(self):
        for tier in ("standard", "heavy"):
            markdown = render_presentation(_report(tier))
            for number, title in enumerate(("头部", "结论", "审查重点", "证据", "过程", "成本", "记忆")):
                self.assertIn(f"## {number}. {title}", markdown)

    def test_unknown_sources_statements_and_cost_coverage_are_explicit_and_safe(self):
        markdown = render_presentation(_report())
        self.assertIn("🤖 机器", markdown)
        self.assertIn("👤 人工", markdown)
        self.assertIn("CONTENT_NOT_MACHINE_VERIFIED", markdown)
        self.assertIn("作者：未知", markdown)
        self.assertIn("内容验证：未经过机器事实验证", markdown)
        self.assertIn("STATEMENT_PROVENANCE_NOT_PERSISTED", markdown)
        self.assertIn("已知费用小计", markdown)
        self.assertIn("完整费用总计", markdown)
        self.assertIn("未提供", markdown)
        self.assertNotIn("<script>", markdown)
        self.assertNotIn("<b>", markdown)
        self.assertIn("C\\|&lt;1&gt;", markdown)
        self.assertIn("## 判据自证", markdown)

    def test_rendering_is_pure_and_receipt_preserves_frozen_presentation(self):
        report = _report()
        before = json.dumps(report, ensure_ascii=False, sort_keys=True)
        first = merge_receipt_bytes(report, "a" * 40)
        second = merge_receipt_bytes(report, "a" * 40)
        self.assertEqual(first, second)
        self.assertEqual(json.loads(first[0])["presentation"], report["presentation"])
        self.assertEqual(json.dumps(report, ensure_ascii=False, sort_keys=True), before)
        self.assertIn("a" * 40, first[1].decode("utf-8"))
        merge_block = first[1].decode("utf-8").split("### merge_commit", 1)[1].split("## 1.", 1)[0]
        self.assertIn("`AVAILABLE`", merge_block)
        self.assertNotIn("PENDING", merge_block)

    def test_legacy_receipt_markdown_stays_byte_identical(self):
        report = json.loads((FIXTURES / "report_legacy_input.json").read_text(encoding="utf-8"))
        actual = merge_receipt_bytes(report, "a" * 40)[1]
        expected = (FIXTURES / "report_legacy_receipt.md").read_bytes()
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
