"""Every agent call is metered (wall time always, cost when the adapter reports it)."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

from storyloop.baseline import bootstrap_project, load_project_config
from storyloop.project_agents import invoke_project_agent
from storyloop.project_loop import load_run_state, run_change
from storyloop.storage import write_json
from storyloop.telemetry import adopt_pending, summarize
from tests.test_baseline_behavior import repository
from tests.test_workspace_scratch import _approved, _services

ECHO = '''
import json, sys
request = json.load(sys.stdin)
if request["role"] == "product":
    result = {"version": 1, "goal": "g", "non_goals": [], "questions": [], "stories": []}
else:
    result = {"summary": "ok", "questions": []}
result["_telemetry"] = {"cost_usd": 0.12, "turns": 3, "duration_ms": 1500, "model": "claude-x"}
print(json.dumps(result))
'''


def _command_project(root: Path) -> None:
    repository(root)
    bootstrap_project(root)
    script = root / ".storyloop/echo_agent.py"
    script.write_text(ECHO, encoding="utf-8")
    config = load_project_config(root)
    config["adapter"] = "command"
    config["agent_command"] = [sys.executable, str(script)]
    write_json(root / ".storyloop/config.json", config)


class TelemetryTests(unittest.TestCase):
    def test_adapter_telemetry_is_stripped_from_results_and_recorded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _command_project(root)
            result = invoke_project_agent(root, "implement", {"change": {"change_id": "FEAT-1"}})
            self.assertEqual(set(result), {"summary", "questions"})
            summary = summarize(root, "FEAT-1")
            self.assertEqual(summary["calls"], 1)
            self.assertAlmostEqual(summary["cost_usd"], 0.12)
            self.assertEqual(summary["by_role"]["implement"]["turns"], 3)
            self.assertGreater(summary["agent_ms"], 0)

    def test_product_calls_before_a_change_exists_are_adopted_later(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _command_project(root)
            invoke_project_agent(root, "product", {"idea": "orders"})
            self.assertEqual(summarize(root, "pending")["calls"], 1)
            adopt_pending(root, "FEAT-2")
            self.assertEqual(summarize(root, "FEAT-2")["calls"], 1)
            self.assertEqual(summarize(root, "pending")["calls"], 0)

    def test_report_carries_cost_and_time_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = _approved(root, {})
            done = run_change(root, change_id, _services(change_id, build_in_place=False))
            self.assertEqual(done["status"], "PASSED", done)
            state = load_run_state(root, change_id)
            self.assertIn("started_at", state)
            report = json.loads(Path(done["report"]).read_text(encoding="utf-8"))
            telemetry = report["telemetry"]
            self.assertEqual(telemetry["calls"], 0)
            self.assertIsNone(telemetry["cost_usd"])
            self.assertGreaterEqual(telemetry["loop_ms"], 0)
            markdown = Path(done["report"]).with_suffix(".md").read_text(encoding="utf-8")
            self.assertIn("## 成本与耗时", markdown)


if __name__ == "__main__":
    unittest.main()
