import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.base_upgrade import propose_base_change, upgrade_baseline
from storyloop.baseline import bootstrap_project
from storyloop.call_log import open_call
from storyloop.change_log import _call_view, render_log
from storyloop.drafts import create_draft
from storyloop.next_steps import append_pending_memory
from storyloop.report_memory import memory_items
from storyloop.report_evidence import collect_evidence
from storyloop.report_markdown import render_presentation
from storyloop.storage import write_json
from tests.lesson_support import commit_all, git_repo, head, sample


CHANGE = {"change_id": "FEAT-1"}
REPORT = {}


class ReportMemoryTests(unittest.TestCase):
    def test_not_configured(self):
        with tempfile.TemporaryDirectory() as folder:
            items = memory_items(Path(folder), CHANGE, REPORT, {"entries": {"ledger": []}})
        self.assertEqual(items[0]["reason"], "LESSONS_NOT_CONFIGURED")

    def test_invalid_layer_diagnostic_reaches_collected_markdown(self):
        from storyloop.call_log import record_lessons_skipped
        from storyloop.lesson_retrieval import lessons_payload
        project = git_repo(raw_lessons={'L-0001.md': 'invalid'})
        provided, reason = lessons_payload(project, 'implement', {})
        self.assertIsNone(provided)
        call = open_call(project, 'implement', 'FEAT-1', 'offline', {})
        record_lessons_skipped(project, call, reason)
        from storyloop.telemetry import record_call
        record_call(project, 'implement', 'FEAT-1', {}, 1, call=call)
        evidence = collect_evidence(project, CHANGE, REPORT)
        self.assertEqual(evidence['entries']['calls'][0]['lessons_skipped'], reason)
        items = memory_items(project, CHANGE, REPORT, evidence)
        rendered = render_presentation({'presentation': {'version': 1, 'tier': 'heavy',
            'sections': {'6': {'title': '记忆', 'items': items}}, 'reconciliation': {}}})
        self.assertIn('LESSON_INVALID', rendered)
        self.assertIn(call, rendered)
        self.assertIn('整层跳过', rendered)
        self.assertIn('代理声称使用', rendered)

    def test_malformed_skip_diagnostic_is_disclosed_and_bounded(self):
        project = git_repo(lessons=[sample()])
        call = open_call(project, 'implement', 'FEAT-1', 'offline', {})
        from storyloop.call_log import record_lesson_evidence
        for value in ({'unexpected': 'shape'}, '', 'x' * 20001):
            with self.subTest(value_type=type(value).__name__):
                record_lesson_evidence(project, call, {'lessons_skipped': value})
                evidence = collect_evidence(project, CHANGE, REPORT)
                reason = evidence['entries']['calls'][0]['lessons_skipped']
                self.assertIsInstance(reason, str)
                self.assertLessEqual(len(reason), 20000)
                if not isinstance(value, str) or not value:
                    self.assertEqual(reason, 'LESSONS_SKIPPED_INVALID')
                else:
                    self.assertTrue(evidence['entries']['truncated']['calls'])

    def test_legacy_invalid_root_is_not_reported_as_unconfigured(self):
        project = git_repo(files={'openspec/lessons': 'invalid root'})
        items = memory_items(project, CHANGE, REPORT, {
            'run': {'contract_commit': head(project)}, 'entries': {'ledger': []}})
        self.assertNotIn('LESSONS_NOT_CONFIGURED', str(items))
        self.assertIn('LESSONS_DIGEST_UNAVAILABLE', str(items))

    def test_provided_and_claimed_per_call(self):
        project = git_repo(lessons=[sample()])
        evidence = {"run": {"contract_commit": head(project)}, "entries": {"ledger": [{
            "call": "c1", "role": "implement", "lessons_provided": ["L-0001", "L-0002"],
            "lessons_claimed": ["L-0001"], "lessons_unknown": ["L-0404"],
        }]}}
        items = {item["id"]: item for item in memory_items(project, CHANGE, REPORT, evidence)}
        self.assertEqual(items["lessons_summary"]["data"]["unknown"], ["L-0404"])
        self.assertEqual(items["lessons_provided"]["reason"], "AGENT_CLAIMED_NOT_VERIFIED")

    def test_truncated_ledger_recovers_complete_meta_and_discloses_damage(self):
        project = git_repo(lessons=[sample()])
        call = "call-1"
        meta = project / ".storyloop/state/telemetry/calls" / call / "meta.json"
        meta.parent.mkdir(parents=True)
        write_json(meta, {"call": call, "lessons_provided": [f"L-{n:04d}" for n in range(1, 23)],
                          "lessons_claimed": ["L-0001"], "lessons_unknown": []})
        evidence = {"run": {"contract_commit": head(project)}, "entries": {"ledger": [{
            "call": call, "role": "implement", "lessons_provided": [f"L-{n:04d}" for n in range(1, 21)],
            "lessons_claimed": ["L-0001"], "lessons_unknown": [], "lessons_truncated": True,
        }]}}
        items = {item["id"]: item for item in memory_items(project, CHANGE, REPORT, evidence)}
        self.assertEqual(len(items["lessons_summary"]["data"]["provided"]), 22)
        meta.write_text("{}", encoding="utf-8")
        damaged = {item["id"]: item for item in memory_items(project, CHANGE, REPORT, evidence)}
        self.assertEqual(damaged["lessons_provided"]["availability"], "NOT_AVAILABLE")
        self.assertIn("COMPLETE_EVIDENCE", damaged["lessons_provided"]["reason"])

    def test_frozen_layer_survives_missing_workspace_directory(self):
        project = git_repo(lessons=[sample()])
        frozen = head(project)
        (project / 'openspec/lessons').rename(project / 'workspace-lesson-backup')
        items = memory_items(project, CHANGE, REPORT, {
            'run': {'contract_commit': frozen}, 'entries': {'ledger': []}})
        self.assertNotIn('LESSONS_NOT_CONFIGURED', str(items))
        summary = next(item for item in items if item['id'] == 'lessons_summary')
        self.assertIsNotNone(summary['data']['digest'])

    def test_legacy_digest_uses_contract_commit_without_writing_report(self):
        project = git_repo(lessons=[sample()])
        report_path = project / "reports/storyloop/FEAT-1/report.json"
        report_path.parent.mkdir(parents=True)
        report_path.write_text(json.dumps({"historic": True}), encoding="utf-8")
        before = report_path.read_bytes()
        items = {item["id"]: item for item in memory_items(
            project, CHANGE, REPORT, {"run": {"contract_commit": head(project)}, "entries": {"ledger": []}})}
        self.assertIsNotNone(items["lessons_summary"]["data"]["digest"])
        self.assertEqual(report_path.read_bytes(), before)

    def test_frozen_digest_is_allowlisted_and_preferred_even_when_none(self):
        project = git_repo(lessons=[sample()])
        run_path = project / ".storyloop/state/runs/FEAT-1.json"
        write_json(run_path, {"change_id": "FEAT-1", "lessons_digest": "frozen-digest"})
        evidence = collect_evidence(project, CHANGE, REPORT)
        self.assertEqual(evidence["run"]["lessons_digest"], "frozen-digest")
        with patch("storyloop.report_memory.lessons_digest",
                   side_effect=AssertionError("frozen digest must not be recomputed")):
            items = {item["id"]: item for item in memory_items(project, CHANGE, REPORT, evidence)}
        self.assertEqual(items["lessons_summary"]["data"]["digest"], "frozen-digest")

        evidence["run"]["lessons_digest"] = None
        with patch("storyloop.report_memory.lessons_digest",
                   side_effect=AssertionError("configured None is also frozen")):
            items = {item["id"]: item for item in memory_items(project, CHANGE, REPORT, evidence)}
        self.assertIsNone(items["lessons_summary"]["data"]["digest"])

    def test_markdown_says_claimed_not_read(self):
        presentation = {"version": 1, "tier": "heavy", "sections": {
            "6": {"title": "记忆", "items": [{"id": "lessons_provided", "source": "machine",
                "availability": "AVAILABLE", "reason": "AGENT_CLAIMED_NOT_VERIFIED", "data": [{
                    "call": "c1", "role": "implement", "provided": ["L-0001"],
                    "claimed": ["L-0001"], "unknown": []}]}]}}, "reconciliation": {}}
        text = render_presentation({"presentation": presentation})
        self.assertIn("代理声称使用", text)
        self.assertNotIn("已读", text)

    def test_next_mentions_pending_memory_drafts_without_changing_existing_text(self):
        project = git_repo()
        create_draft(project, "lesson", "FEAT-1", None, {}, "正文")
        original = "原有停止说明"
        value = append_pending_memory(project, original)
        self.assertTrue(value.startswith(original + "\n"))
        self.assertIn("有 1 个待批经验或勘误草案：storyloop drafts", value)

    def test_log_displays_retrospective_mode_and_lessons_summary(self):
        text = render_log({"change_id": "FEAT-1", "change_status": "PASSED", "run_status": "PASSED",
                           "calls": [{"role": "review", "mode": "retrospective"}], "failures": [],
                           "agent_failures": [], "telemetry": {}, "lessons": {"retrospective": "DONE"}})
        self.assertIn("retrospective", text)
        self.assertIn("经验复盘：DONE", text)

    def test_log_recognizes_retrospective_request(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            call = open_call(project, "review", "FEAT-1", "offline", {
                "payload": {"mode": "retrospective"}})
            view = _call_view(project, {"call": call, "role": "review"})
        self.assertEqual(view["mode"], "retrospective")

    def test_real_base_update_lists_scoped_active_lessons_and_does_not_fail_on_diagnostics(self):
        project = git_repo(lessons=[sample(scope=["core.py"])], files={"core.py": "VALUE = 1\n"})
        bootstrap_project(project, {"test_commands": [["python", "-c", "pass"]]})
        proposal = propose_base_change(project, ["core.py"], "update core")
        (project / "core.py").write_text("VALUE = 2\n", encoding="utf-8")
        commit_all(project, "base update")
        from tests.test_base_upgrade import begin_legacy_receipt
        begin_legacy_receipt(project, project / ".storyloop/base-proposals" /
                             f"{proposal['change_id']}.json")
        result = upgrade_baseline(project, proposal["change_id"])
        self.assertEqual(result["lessons_to_review"], ["L-0001"])

        project = git_repo(lessons=[sample(scope=["core.py"])], files={"core.py": "VALUE = 1\n"})
        bootstrap_project(project, {"test_commands": [["python", "-c", "pass"]]})
        proposal = propose_base_change(project, ["core.py"], "update core")
        (project / "core.py").write_text("VALUE = 2\n", encoding="utf-8")
        commit_all(project, "base update")
        begin_legacy_receipt(project, project / ".storyloop/base-proposals" /
                             f"{proposal['change_id']}.json")
        with patch("storyloop.base_upgrade._lessons_to_review", side_effect=ValueError("bad memory")):
            result = upgrade_baseline(project, proposal["change_id"])
        self.assertEqual(result["status"], "BASELINE_UPGRADED")
        self.assertIn("bad memory", result["lessons_to_review_error"])


if __name__ == "__main__":
    unittest.main()
