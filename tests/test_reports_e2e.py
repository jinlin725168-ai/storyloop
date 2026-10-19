"""Closed-loop report proof with real Git and a deterministic simulated adapter."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project, load_project_config
from storyloop.models import StoryLoopError
from storyloop.project_agents import invoke_project_agent
from storyloop.project_loop import run_change
from storyloop.storage import load_json, write_json
from storyloop.telemetry import ledger_entries
from tests.routing_support import legacy_create_change as create_change
from tests.support import product
from tests.test_baseline_behavior import git, repository


class ReportsEndToEndTests(unittest.TestCase):
    def test_native_command_adapter_closes_git_loop_with_traceable_report(self):
        """The adapter is simulated; Git, call records, report, merge and receipt are real."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root, {"test_commands": []})
            state = create_change(root, "report provenance", product())
            change_id = state["change_id"]
            approve_change(root, change_id)
            self._install_deterministic_command_adapter(root)

            with self.assertRaises(StoryLoopError) as invalid:
                invoke_project_agent(root, "review", {
                    "change": {"change_id": change_id}, "fixture_failure": True,
                })
            self.assertEqual(invalid.exception.code, "AGENT_CONTRACT_INVALID")

            result = run_change(root, change_id)
            report_path = Path(result["report"])
            report = load_json(report_path)
            markdown = report_path.with_suffix(".md").read_text(encoding="utf-8")
            entries = ledger_entries(root, change_id)

            self.assertEqual(result["status"], "PASSED")
            self._assert_presentation_and_calls(root, report, markdown, entries)
            self._assert_receipt_and_repository(root, change_id, result, report)

    def _assert_presentation_and_calls(self, root, report, markdown, entries):
        self.assertEqual(set(report["presentation"]["sections"]), set("0123456"))
        reconciliation = report["presentation"]["reconciliation"]
        self.assertEqual(reconciliation["status"], "MATCH")
        self.assertTrue(all(check["status"] != "MISMATCH" for check in reconciliation["checks"]))
        self.assertIn("## 0. 头部", markdown)
        self.assertIn("## 6. 记忆", markdown)
        self.assertEqual(len(entries), 4)
        self.assertTrue(all(entry.get("call") for entry in entries))
        metadata = [load_json(root / ".storyloop/state/telemetry/calls" / entry["call"] / "meta.json")
                    for entry in entries]
        self.assertEqual(sorted(item["status"] for item in metadata), ["FAILED", "OK", "OK", "OK"])

        telemetry = report["telemetry"]
        self.assertEqual(telemetry["calls"], len(entries))
        self.assertEqual(telemetry["failed"], 1)
        self.assertIsNone(telemetry["cost_usd"])
        self.assertEqual(telemetry["known_cost_usd"], 0.25)
        self.assertEqual(telemetry["coverage"]["cost_usd"], {"known": 1, "total": 4})
        self.assertTrue(any(entry.get("failed") for entry in entries))
        self.assertTrue(all(entry.get("skills") is not None for entry in entries))
        skills = self._presentation_item(report, "3", "skills")["data"]
        self.assertEqual({item["call"] for item in skills}, {entry["call"] for entry in entries})
        self.assertTrue(all(item["availability"] == "AVAILABLE" for item in skills))

    def _assert_receipt_and_repository(self, root, change_id, result, report):
        receipt_parent = git(root, "show", f"{result['integration_commit']}^:"
                             f"reports/storyloop/{change_id}/report.json")
        pre_receipt = json.loads(receipt_parent)
        self.assertEqual(report["presentation"], pre_receipt["presentation"])
        self.assertEqual(report["merge_commit"], result["merge_commit"])
        self.assertEqual(git(root, "rev-parse", "HEAD"), result["integration_commit"])
        self.assertEqual(git(root, "status", "--porcelain=v1", "--untracked-files=all"), "")

    @staticmethod
    def _presentation_item(report: dict, section: str, identifier: str) -> dict:
        return next(item for item in report["presentation"]["sections"][section]["items"]
                    if item["id"] == identifier)

    @staticmethod
    def _install_deterministic_command_adapter(root: Path) -> None:
        script = root / ".storyloop" / "deterministic-report-adapter.py"
        script.write_text(textwrap.dedent("""
            import json
            from pathlib import Path
            import sys

            request = json.load(sys.stdin)
            role = request["role"]
            payload = request["payload"]
            telemetry = {"model": "fixture-model", "vendor": "fixture-vendor",
                         "tier_applied": "fixture-tier", "turns": 1}
            if payload.get("fixture_failure"):
                telemetry["cost_usd"] = 0.25
                answer = {"summary": "intentionally invalid fixture response",
                          "_telemetry": telemetry}
            elif role == "acceptance":
                change_id = payload["change"]["change_id"]
                source = ("from pathlib import Path\\n"
                          "p = Path('modules/orders/service.py')\\n"
                          "raise SystemExit(0 if p.is_file() and "
                          "p.read_text().strip() == 'VALUE = 1' else 1)\\n")
                answer = {"files": [{"path": "flow.py", "content": source}],
                          "manifest": {"version": 1, "tests": [{
                              "id": "SMOKE-REPORT", "criteria": ["AC-1"],
                              "command": ["{python}",
                                          f"acceptance/changes/{change_id}/flow.py"],
                              "timeout_seconds": 10}]}, "_telemetry": telemetry}
            elif role == "implement":
                target = Path("modules/orders/service.py")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("VALUE = 1\\n", encoding="utf-8")
                answer = {"summary": "deterministic fixture implementation",
                          "questions": [], "facts": [{"statement": "service value written",
                          "location": "modules/orders/service.py:1"}], "_telemetry": telemetry}
            elif role == "review":
                answer = {"verdict": "pass", "summary": "deterministic fixture review",
                          "findings": [], "questions": [], "facts": [],
                          "_telemetry": telemetry}
            else:
                raise SystemExit(f"unexpected role: {role}")
            print(json.dumps(answer))
        """).lstrip(), encoding="utf-8")
        config = load_project_config(root)
        config.update({"adapter": "command", "agent_command": [sys.executable, str(script)]})
        write_json(root / ".storyloop/config.json", config)


if __name__ == "__main__":
    unittest.main()
