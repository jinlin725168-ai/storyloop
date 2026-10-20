"""Per-call records, `storyloop log` and next-step hints."""

from __future__ import annotations

import io
import json
import shlex
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop import cli
from storyloop.call_log import CALLS_ROOT
from storyloop.change_log import change_log, render_log
from storyloop.models import StoryLoopError
from storyloop.project_agents import invoke_project_agent
from storyloop.project_loop import LoopServices, run_change
from storyloop.telemetry import ledger_entries
from tests.test_codex_adapter import THREAD, project as codex_project
from tests.test_project_loop import smoke_payload
from tests.test_structured_output import (approved_change, codex_call, command_project,
                                          contract_breaking_implement, echo_script, passing_review)


def call_folder(root: Path, change_id: str) -> Path:
    entry = ledger_entries(root, change_id)[-1]
    return root / CALLS_ROOT / entry["call"]


class CallRecordTests(unittest.TestCase):
    def test_every_call_leaves_a_redacted_request_and_its_answer(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            codex_project(root)
            payload = {"change": {"change_id": "FEAT-9", "note": "password=hunter2"}}
            codex_call(root, ['{"summary": "done", "questions": []}'], payload=payload)
            folder_ = call_folder(root, "FEAT-9")
            request = json.loads((folder_ / "request.json").read_text(encoding="utf-8"))
            self.assertEqual(request["payload"]["change"]["note"], "password=[REDACTED]")
            self.assertIn("response_schema", request)
            response = json.loads((folder_ / "response.json").read_text(encoding="utf-8"))
            self.assertEqual(response["summary"], "done")
            meta = json.loads((folder_ / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual((meta["status"], meta["sessions"]), ("OK", [THREAD]))
            self.assertEqual(ledger_entries(root, "FEAT-9")[-1]["session"], THREAD)

    def test_a_repaired_answer_records_its_repairs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            codex_project(root)
            codex_call(root, ['{"summary": "done", "questions": [], "notes": "x"}',
                              '{"summary": "done", "questions": []}'])
            meta = json.loads((call_folder(root, "FEAT-9") / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual((meta["status"], meta["repairs"]), ("OK", 1))

    def test_a_contract_failure_keeps_the_raw_answer_and_its_problems(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            codex_project(root)
            wrong = '{"summary": 1, "questions": []}'
            with self.assertRaises(StoryLoopError):
                codex_call(root, [wrong, wrong, wrong])
            self.assertTrue(ledger_entries(root, "FEAT-9")[-1]["failed"])
            folder_ = call_folder(root, "FEAT-9")
            meta = json.loads((folder_ / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["code"], "AGENT_CONTRACT_INVALID")
            self.assertIn("$.summary: expected string, got integer", meta["schema_errors"])
            self.assertEqual((folder_ / "response.txt").read_text(encoding="utf-8"), wrong)

    def test_the_adapters_full_stderr_is_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            command_project(root, echo_script({"summary": "ok", "questions": []}))
            invoke_project_agent(root, "implement", {"change": {"change_id": "FEAT-3"}})
            stderr = (call_folder(root, "FEAT-3") / "stderr.log").read_text(encoding="utf-8")
            self.assertIn("schema keys: summary,questions,design", stderr)


class LogAndNextStepTests(unittest.TestCase):
    def test_log_names_the_failed_gate_of_each_attempt(self):
        temporary, root, change_id = approved_change()
        services = LoopServices(generate_smoke=lambda project, state: smoke_payload(change_id),
                                implement=contract_breaking_implement([]), review=passing_review)
        try:
            self.assertEqual(run_change(root, change_id, services)["status"], "PASSED")
            log = change_log(root, change_id)
            self.assertEqual(log["failures"][0]["gate"], "contract")
            self.assertIn("storyloop report", log["next"])
            self.assertIn("第 1 轮  contract", render_log(log))
        finally:
            temporary.cleanup()

    def test_every_pause_names_its_next_command(self):
        temporary, root, change_id = approved_change()
        services = LoopServices(
            generate_smoke=lambda project, state: smoke_payload(change_id),
            implement=lambda project, context: {"summary": "nothing", "questions": []},
            review=passing_review)
        try:
            result = run_change(root, change_id, services)
            self.assertEqual(result["status"], "NO_PROGRESS")
            # The engine names the resolved project path (on Windows the temp dir may be an 8.3 short name).
            # Hints are shell-quoted, so a Windows path (backslashes) appears in single quotes.
            self.assertIn(f"storyloop run {shlex.quote(str(root.resolve()))} --change {change_id}",
                          result["next"])
            # A started run's contract cannot be revised, so the hint must not suggest it.
            self.assertNotIn("修订合同", result["next"])
            self.assertTrue(result["next"])
            with patch("sys.stdout", new_callable=io.StringIO) as out:
                code = cli.main(["status", str(root), "--change", change_id, "--json"])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(out.getvalue())["status"], "NO_PROGRESS")
            self.assertIn("storyloop run", json.loads(out.getvalue())["next"])
            with patch("sys.stdout", new_callable=io.StringIO) as out:
                code = cli.main(["log", str(root), "--change", change_id])
            self.assertEqual(code, 0)
            text = out.getvalue()
            self.assertIn("失败的尝试：", text)
            self.assertIn("acceptance=FAILED", text)
            self.assertIn("恢复：", text)
        finally:
            temporary.cleanup()


class ExitCodeTests(unittest.TestCase):
    def test_stops_that_need_a_human_fix_exit_2_and_manual_acceptance_exits_0(self):
        expected = {"CHARACTERIZATION_FAILED": 2, "BUDGET_EXHAUSTED": 2, "NO_PROGRESS": 2,
                    "NEEDS_SPEC_FIX": 2, "NEEDS_INPUT": 2,
                    "SMOKE_UPDATE_FAILED": 2, "EXISTING_GREEN": 2, "MANUAL_ACCEPTANCE_REQUIRED": 0, "PASSED": 0,
                    "SMOKE_APPLIED": 0}
        for status, code in expected.items():
            with self.subTest(status=status), \
                    patch("storyloop.cli.dispatch", return_value={"status": status, "change_id": "FEAT-1"}), \
                    patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(cli.main(["run", ".", "--change", "FEAT-1"]), code)


if __name__ == "__main__":
    unittest.main()
