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
from storyloop.skills import ROLE_SKILLS, instruction_skill_snapshot
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

    def test_skill_snapshot_check_rejects_empty_or_incomplete_provenance(self):
        expected = [{"name": "oracle-craft", "path": "storyloop/skills/oracle-craft/SKILL.md",
                     "version": "1", "sha256": "a" * 64}]
        section = [{"call": "call-1", "role": "review", "skills": expected,
                    "availability": "AVAILABLE"}]

        empty = [{"call": "call-1", "role": "review", "skills": []}]
        missing_hash = [{"call": "call-1", "role": "review",
                         "skills": [{key: value for key, value in expected[0].items()
                                     if key != "sha256"}]}]
        empty_section = [{"call": "call-1", "role": "review", "skills": [],
                          "availability": "AVAILABLE"}]

        self.assertTrue(self._skill_snapshot_errors(empty, section, {"review": expected}))
        self.assertTrue(self._skill_snapshot_errors(missing_hash, section, {"review": expected}))
        self.assertTrue(self._skill_snapshot_errors(
            [{"call": "call-1", "role": "review", "skills": expected}],
            empty_section, {"review": expected}))

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
        skills = self._presentation_item(report, "3", "skills")["data"]
        roles = {entry["role"] for entry in entries}
        expected = {role: [instruction_skill_snapshot(root, name)[1]
                           for name in ROLE_SKILLS.get(role, ())] for role in roles}
        self.assertEqual(expected["implement"], [])
        self.assertTrue(expected["acceptance"])
        self.assertTrue(expected["review"])
        for snapshot in expected["acceptance"] + expected["review"]:
            self.assertEqual(set(snapshot), {"name", "path", "version", "sha256"})
            self.assertTrue(snapshot["path"] and snapshot["version"])
            self.assertEqual(len(snapshot["sha256"]), 64)
        self.assertEqual(self._skill_snapshot_errors(entries, skills, expected), [])

    @staticmethod
    def _skill_snapshot_errors(entries, section, expected_by_role):
        errors = []
        section_by_call = {item.get("call"): item for item in section}
        if len(section_by_call) != len(section) or len(section) != len(entries):
            errors.append("call set differs")
        for entry in entries:
            expected = expected_by_role[entry["role"]]
            if entry.get("skills") != expected:
                errors.append(f"{entry['call']}: ledger snapshot differs")
            item = section_by_call.get(entry["call"])
            if item is None or item.get("role") != entry["role"]:
                errors.append(f"{entry['call']}: report call is missing")
            elif item.get("availability") != "AVAILABLE" or item.get("skills") != entry.get("skills"):
                errors.append(f"{entry['call']}: report snapshot differs")
        return errors

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
