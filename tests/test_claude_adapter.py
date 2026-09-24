"""The Claude Code adapter resumes the same session when a segment hits its turn cap."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "claude_agent", Path(__file__).resolve().parents[1] / "examples/claude-code/claude_agent.py")
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


def envelope(subtype: str, result: str, session: str, cost: float, turns: int) -> str:
    return json.dumps({"type": "result", "subtype": subtype, "is_error": subtype != "success",
                       "result": result, "session_id": session, "total_cost_usd": cost,
                       "num_turns": turns, "duration_ms": 1000, "usage": {"output_tokens": 5},
                       "modelUsage": {"claude-x": {}}})


class Completed:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout, self.stderr, self.returncode = stdout, "", returncode


class ClaudeAdapterTests(unittest.TestCase):
    def test_turn_cap_resumes_the_session_and_sums_telemetry(self):
        calls = []
        replies = [envelope("error_max_turns", "", "sid-1", 0.5, 60),
                   envelope("error_max_turns", "", "sid-1", 0.4, 60),
                   envelope("success", '{"summary":"done","questions":[]}', "sid-1", 0.1, 7)]

        def fake_run(command, **kwargs):
            calls.append((list(command), kwargs.get("input")))
            reply = replies[len(calls) - 1]
            return Completed(reply, returncode=0 if '"success"' in reply else 1)  # claude exits 1 on max turns

        request = {"role": "implement", "project": "/tmp/p", "payload": {}, "instructions": "do it"}
        with patch.object(adapter.subprocess, "run", fake_run), \
                patch.object(adapter.shutil, "which", return_value="/usr/bin/claude"), \
                patch("sys.stdin", io.StringIO(json.dumps(request))), \
                patch("sys.stdout", new_callable=io.StringIO) as out:
            code = adapter.main_with_args(["--segment-turns", "60", "--max-segments", "5"])
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 3)
        self.assertNotIn("--resume", calls[0][0])
        self.assertIn("--resume", calls[1][0])
        self.assertEqual(calls[1][0][calls[1][0].index("--resume") + 1], "sid-1")
        self.assertIn("继续", calls[1][1])
        result = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(result["summary"], "done")
        self.assertAlmostEqual(result["_telemetry"]["cost_usd"], 1.0)
        self.assertEqual(result["_telemetry"]["turns"], 127)
        self.assertEqual(result["_telemetry"]["segments"], 3)

    def test_segment_budget_exhausted_is_a_failure(self):
        replies = [envelope("error_max_turns", "", "sid-2", 0.5, 60)] * 3
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            return Completed(replies[len(calls) - 1], returncode=1)

        request = {"role": "review", "project": "/tmp/p", "payload": {}, "instructions": "check"}
        with patch.object(adapter.subprocess, "run", fake_run), \
                patch.object(adapter.shutil, "which", return_value="/usr/bin/claude"), \
                patch("sys.stdin", io.StringIO(json.dumps(request))), \
                patch("sys.stdout", new_callable=io.StringIO), \
                patch("sys.stderr", new_callable=io.StringIO) as err:
            code = adapter.main_with_args(["--segment-turns", "60", "--max-segments", "3",
                                           "--review-perspectives", "none"])
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 3)
        self.assertIn("segments", err.getvalue())

    def test_a_prose_ending_is_repaired_by_asking_the_session_for_its_json(self):
        replies = [envelope("success", "Done. The suite is written under acceptance/changes/…", "sid-3", 0.4, 30),
                   envelope("success", json.dumps({"files": [], "manifest": {"version": 1, "tests": []}}), "sid-3", 0.1, 1)]
        calls = []
        inputs = []

        def fake_run(command, **kwargs):
            calls.append(command)
            inputs.append(kwargs.get("input"))
            return Completed(replies[len(calls) - 1], returncode=0)

        request = {"role": "acceptance", "project": "/tmp/p", "payload": {}, "instructions": "write"}
        with patch.object(adapter.subprocess, "run", fake_run), \
                patch.object(adapter.shutil, "which", return_value="/usr/bin/claude"), \
                patch("sys.stdin", io.StringIO(json.dumps(request))), \
                patch("sys.stdout", new_callable=io.StringIO) as out, \
                patch("sys.stderr", new_callable=io.StringIO) as err:
            code = adapter.main_with_args([])
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        self.assertIn("--resume", calls[1])
        self.assertEqual(inputs[1], adapter.REPAIR_PROMPT)
        self.assertIn("repair 1/2", err.getvalue())
        self.assertEqual(json.loads(out.getvalue())["manifest"]["version"], 1)


class ReviewPerspectiveTests(unittest.TestCase):
    """review fans out to perspective sub-agents (parallel claude sessions) and a lead merges the verdict."""

    def _request(self):
        return {"role": "review", "project": "/tmp/p", "instructions": "review it",
                "payload": {"change": {"change_id": "FEAT-1"}, "layers": {}, "design_plan": None}}

    def test_perspectives_run_and_the_lead_merges_them_into_one_verdict(self):
        calls = []

        def fake_run(command, **kwargs):
            prompt = kwargs.get("input", "")
            calls.append(prompt)
            if adapter.LEAD_MARK in prompt:
                self.assertIn("security", prompt)  # the lead sees every perspective's output
                self.assertIn("SQL built by string concatenation", prompt)
                result = {"verdict": "fail", "summary": "security found a real defect",
                          "findings": [{"perspective": "security", "message": "SQL built by string concatenation"}],
                          "questions": []}
                return Completed(envelope("success", json.dumps(result), "lead", 0.2, 3))
            for name in adapter.REVIEW_PERSPECTIVES:
                if adapter.PERSPECTIVE_MARK.format(name=name) in prompt:
                    findings = ([{"message": "SQL built by string concatenation", "evidence": "Mapper.xml:12"}]
                                if name == "security" else [])
                    result = {"summary": f"{name} looked", "findings": findings, "questions": []}
                    return Completed(envelope("success", json.dumps(result), f"sid-{name}", 0.1, 2))
            raise AssertionError("unexpected prompt")

        with patch.object(adapter.subprocess, "run", fake_run), \
                patch.object(adapter.shutil, "which", return_value="/usr/bin/claude"), \
                patch("sys.stdin", io.StringIO(json.dumps(self._request()))), \
                patch("sys.stdout", new_callable=io.StringIO) as out, \
                patch("sys.stderr", new_callable=io.StringIO):
            code = adapter.main_with_args([])
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), len(adapter.REVIEW_PERSPECTIVES) + 1)
        result = json.loads(out.getvalue())
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["findings"][0]["perspective"], "security")
        telemetry = result["_telemetry"]
        self.assertEqual(telemetry["perspectives"], len(adapter.REVIEW_PERSPECTIVES))
        self.assertAlmostEqual(telemetry["cost_usd"], 0.1 * len(adapter.REVIEW_PERSPECTIVES) + 0.2)
        self.assertEqual(telemetry["turns"], 2 * len(adapter.REVIEW_PERSPECTIVES) + 3)

    def test_perspectives_can_be_disabled_for_a_single_review_session(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(kwargs.get("input", ""))
            result = {"verdict": "pass", "summary": "ok", "findings": [], "questions": []}
            return Completed(envelope("success", json.dumps(result), "one", 0.3, 5))

        with patch.object(adapter.subprocess, "run", fake_run), \
                patch.object(adapter.shutil, "which", return_value="/usr/bin/claude"), \
                patch("sys.stdin", io.StringIO(json.dumps(self._request()))), \
                patch("sys.stdout", new_callable=io.StringIO) as out, \
                patch("sys.stderr", new_callable=io.StringIO):
            code = adapter.main_with_args(["--review-perspectives", "none"])
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        self.assertNotIn(adapter.LEAD_MARK, calls[0])
        self.assertEqual(json.loads(out.getvalue())["verdict"], "pass")

    def test_a_failing_perspective_fails_the_whole_review_call(self):
        def fake_run(command, **kwargs):
            prompt = kwargs.get("input", "")
            if adapter.PERSPECTIVE_MARK.format(name="tests") in prompt:
                return Completed(envelope("error_during_execution", "boom", "sid-tests", 0.1, 1), returncode=1)
            result = {"summary": "fine", "findings": [], "questions": []}
            return Completed(envelope("success", json.dumps(result), "sid", 0.1, 2))

        with patch.object(adapter.subprocess, "run", fake_run), \
                patch.object(adapter.shutil, "which", return_value="/usr/bin/claude"), \
                patch("sys.stdin", io.StringIO(json.dumps(self._request()))), \
                patch("sys.stdout", new_callable=io.StringIO), \
                patch("sys.stderr", new_callable=io.StringIO) as err:
            code = adapter.main_with_args([])
        self.assertEqual(code, 1)
        self.assertIn("tests", err.getvalue())


if __name__ == "__main__":
    unittest.main()
