"""Public log output for acceptance retry history."""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from storyloop import cli
from tests.test_structured_output import approved_change


class AcceptanceRetryLogTests(unittest.TestCase):
    def setUp(self):
        self.temporary, self.root, self.change_id = approved_change()
        self.addCleanup(self.temporary.cleanup)
        self.run_path = self.root / ".storyloop/state/runs" / f"{self.change_id}.json"

    def log(self, *, as_json=True):
        args = ["log", str(self.root), "--change", self.change_id]
        if as_json:
            args.append("--json")
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(cli.main(args), 0)
        return json.loads(output.getvalue()) if as_json else output.getvalue()

    def test_rounds_project_changes_repairs_and_stop(self):
        self.run_path.parent.mkdir(parents=True, exist_ok=True)
        self.run_path.write_text(json.dumps({
            "status": "PASSED", "repairs": [{"automatic": True}, {}],
            "acceptance_retry": {"rounds": [
                {"repair_count": 0, "finding_ids": ["B", "A"], "signatures": []},
                {"repair_count": 1, "finding_ids": ["C", "B"], "signatures": []}],
                "stop": {"reason": "round_limit", "round_limit": 2, "finding_ids": ["B"]}},
        }), encoding="utf-8")
        result = self.log()
        self.assertEqual(result["acceptance_rounds"], [
            {"round": 1, "blocking_ids": ["A", "B"], "new_ids": ["A", "B"],
             "resolved_ids": [], "repair": "automatic"},
            {"round": 2, "blocking_ids": ["B", "C"], "new_ids": ["C"],
             "resolved_ids": ["A"], "repair": "human"}])
        self.assertEqual(result["acceptance_retry_stop"],
                         {"reason": "round_limit", "round_limit": 2})
        self.assertIn("验收自动修复\n第 1 轮：阻断 2 条（新增 2，解决 0）：A、B\n"
                      "第 2 轮：阻断 2 条（新增 1，解决 1）：B、C\n"
                      "停止：round_limit（上限 2 轮）", self.log(as_json=False))

    def test_signature_fallback_is_redacted_and_evidence_is_omitted(self):
        self.run_path.parent.mkdir(parents=True, exist_ok=True)
        self.run_path.write_text(json.dumps({
            "acceptance_retry": {"rounds": [{"repair_count": 0, "finding_ids": [],
                "signatures": [{"category": "ORACLE_STRENGTH_PRECHECK",
                                "criterion_id": "AC-1", "path": "a.py",
                                "rule": "token=EXAMPLE_SECRET"}]}]},
            "acceptance": {"evidence": "PRIVATE_EVIDENCE_MARKER"},
        }), encoding="utf-8")
        expected = "ORACLE_STRENGTH_PRECHECK|AC-1|a.py|token=[REDACTED]"
        result = self.log()
        self.assertEqual(result["acceptance_rounds"][0]["blocking_ids"], [expected])
        self.assertNotIn("EXAMPLE_SECRET", json.dumps(result["acceptance_rounds"]))
        self.assertNotIn("PRIVATE_EVIDENCE_MARKER", json.dumps(result["acceptance_rounds"]))
        self.assertIn(expected, self.log(as_json=False))

    def test_distinct_signatures_remain_distinct_after_redaction(self):
        self.run_path.parent.mkdir(parents=True, exist_ok=True)
        signatures = [{"category": "ORACLE_STRENGTH_PRECHECK", "criterion_id": "AC-8",
                       "path": "a.py", "rule": f"token=EXAMPLE_SECRET_{suffix}"}
                      for suffix in ("ONE", "TWO")]
        self.run_path.write_text(json.dumps({"acceptance_retry": {"rounds": [
            {"repair_count": 0, "finding_ids": [], "signatures": signatures}]}}), encoding="utf-8")
        expected = "ORACLE_STRENGTH_PRECHECK|AC-8|a.py|token=[REDACTED]"
        self.assertEqual(self.log()["acceptance_rounds"][0]["blocking_ids"], [expected, expected])
        text = self.log(as_json=False)
        self.assertIn(f"第 1 轮：阻断 2 条（新增 1，解决 0）：{expected}、{expected}", text)
        self.assertNotIn("EXAMPLE_SECRET", text)

    def test_multiline_signature_occupies_one_text_line(self):
        self.run_path.parent.mkdir(parents=True, exist_ok=True)
        self.run_path.write_text(json.dumps({"acceptance_retry": {"rounds": [
            {"repair_count": 0, "finding_ids": [], "signatures": [
                {"category": "ORACLE_STRENGTH_PRECHECK", "criterion_id": "AC-3",
                 "path": "a.py", "rule": "first\nsecond"}]}]}}), encoding="utf-8")
        raw = "ORACLE_STRENGTH_PRECHECK|AC-3|a.py|first\nsecond"
        self.assertEqual(self.log()["acceptance_rounds"][0]["blocking_ids"], [raw])
        section = self.log(as_json=False).split("验收自动修复\n", 1)[1].split("\n\n合计", 1)[0]
        self.assertEqual(section.splitlines(), [
            "第 1 轮：阻断 1 条（新增 1，解决 0）："
            "ORACLE_STRENGTH_PRECHECK|AC-3|a.py|first\\nsecond"])


if __name__ == "__main__":
    unittest.main()
