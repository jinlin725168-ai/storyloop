"""Project final Judge orchestration tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storyloop.final_judges import run_final_judges
from storyloop.models import StoryLoopError
from tests.test_stage_config import loop_config
from tests.test_stage_runner import judge_evidence


class FinalJudgeTests(unittest.TestCase):
    def test_runs_declared_final_judges_in_stage_order(self):
        config = loop_config()
        config["stages"][0]["judges"].append(
            {"id": "regression", "phase": "final", "command": ["python", "final.py"]}
        )
        calls = []

        def judge(project, value, stage, judge_id, context, **kwargs):
            calls.append((stage, judge_id, context["completed_stages"]))
            return judge_evidence(judge_id, "final", "GREEN")

        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.final_judges.run_stage_judge", side_effect=judge):
            evidence = run_final_judges(
                Path(folder), config, ["foundation"], [{"stage": "foundation"}],
            )
        self.assertEqual(calls, [("foundation", "regression", ["foundation"])])
        self.assertEqual(evidence[0]["judge"], "regression")

    def test_final_failure_short_circuits(self):
        config = loop_config()
        config["stages"][0]["judges"].append(
            {"id": "regression", "phase": "final", "command": ["python", "final.py"]}
        )
        with tempfile.TemporaryDirectory() as folder, patch(
            "storyloop.final_judges.run_stage_judge",
            return_value=judge_evidence("regression", "final", "FAIL"),
        ):
            with self.assertRaises(StoryLoopError) as caught:
                run_final_judges(Path(folder), config, ["foundation"], [])
        self.assertEqual(caught.exception.code, "JUDGE_FAILED")


if __name__ == "__main__":
    unittest.main()
