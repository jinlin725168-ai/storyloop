"""Coordinator-level shared deadline coverage for final Judges."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storyloop.stage_coordinator import run_stage_graph
from tests.test_stage_config import loop_config
from tests.test_stage_coordinator import stage_green
from tests.test_stage_runner import judge_evidence


class FinalDeadlineTests(unittest.TestCase):
    def test_all_final_judges_receive_one_project_deadline(self):
        config = loop_config()
        config["stages"][0]["judges"].append(
            {"id": "release", "phase": "final", "command": ["python", "final.py"]}
        )
        clock = lambda: 123
        evidence = judge_evidence("release", "final", "GREEN")
        with tempfile.TemporaryDirectory() as folder, patch(
            "storyloop.stage_coordinator.run_final_judges", return_value=[evidence]
        ) as final:
            project = Path(folder)
            run_stage_graph(
                project, config, {}, project_clock_ms=clock,
                require_governance=False, lifecycle_runner=lambda p, c, s, payload, **kwargs: stage_green(s),
            )
        options = final.call_args.kwargs
        self.assertEqual(options["attempt_deadline_ms"],
                         123 + config["budgets"]["total_seconds"] * 1000)
        self.assertIs(options["budget_clock_ms"], clock)


if __name__ == "__main__":
    unittest.main()
