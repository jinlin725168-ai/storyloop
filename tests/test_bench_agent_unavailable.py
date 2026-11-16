"""An unavailable agent (crash, quota) is an infrastructure gap, not a failed attempt."""

from pathlib import Path
import json
import tempfile
import unittest

from storyloop.agent_transport import _check_process
from storyloop.bench_run import run_bench
from storyloop.bench_worker import _run_until_stop
from storyloop.models import StoryLoopError
from tests.test_bench_case import case_fixture

# 2026-11-16 milestone bench: Codex hit its usage limit after four attempts; the remaining six
# each failed on the first agent call and were recorded as FAILED product attempts.
LIMIT = "You've hit your usage limit. Try again at Nov 18th, 2026 4:52 PM."
UNAVAILABLE = {"status": "AGENT_UNAVAILABLE", "events": [], "calls": 1, "agent_ms": 1,
               "wall_ms": 2, "reason": f"Project agent process failed safely (exit 1 | {LIMIT})"}
PASSED = {"status": "PASSED", "events": [], "calls": 1, "agent_ms": 1, "wall_ms": 2}


class BenchAgentUnavailableTests(unittest.TestCase):
    def run_sequence(self, outcomes):
        calls = []

        def invoke(*args):
            calls.append(args[:3])
            return outcomes[min(len(calls), len(outcomes)) - 1]

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = run_bench([case_fixture(root)], "a" * 40, "b" * 40, "test", "fixture",
                               {"max_calls": 10}, invoke, lambda *_: True, root)
        return result, calls

    def test_two_consecutive_unavailable_attempts_abort_the_run(self):
        result, calls = self.run_sequence([UNAVAILABLE])
        attempts = result["cases"][0]["attempts"]
        self.assertEqual(len(calls), 2)
        self.assertEqual([item["status"] for item in attempts],
                         ["AGENT_UNAVAILABLE", "AGENT_UNAVAILABLE", "NOT_RUN", "NOT_RUN"])
        self.assertEqual({item["verdict"] for item in attempts}, {"NOT_COMPARABLE"})
        self.assertEqual(attempts[0]["termination_reason"], "AGENT_UNAVAILABLE")
        self.assertIn("usage limit", attempts[0]["agent_error"])
        self.assertEqual(result["aborted"]["reason"], "AGENT_UNAVAILABLE")
        self.assertIn("usage limit", result["aborted"]["detail"])

    def test_single_unavailable_attempt_does_not_abort(self):
        result, calls = self.run_sequence([UNAVAILABLE, PASSED])
        self.assertEqual(len(calls), 4)
        self.assertNotIn("aborted", result)
        verdicts = [item["verdict"] for item in result["cases"][0]["attempts"]]
        self.assertEqual(verdicts, ["NOT_COMPARABLE", "PASSED", "PASSED", "PASSED"])

    def test_worker_reports_agent_unavailable_over_saved_phase(self):
        def run(*_):
            raise StoryLoopError(UNAVAILABLE["reason"], code="AGENT_UNAVAILABLE")

        outcome = _run_until_stop(
            Path("/tmp/fixture"), "FEAT-20261116-x", run, lambda *_: None,
            lambda *_: {"status": "PREPARING", "error": UNAVAILABLE["reason"],
                        "intervention_events": []})
        self.assertEqual(outcome["status"], "AGENT_UNAVAILABLE")
        self.assertIn("usage limit", outcome["reason"])

    def test_codex_error_event_reaches_the_failure_detail(self):
        stdout = "\n".join([json.dumps({"type": "thread.started", "thread_id": "t"}),
                            json.dumps({"type": "error", "message": LIMIT}),
                            json.dumps({"type": "turn.failed", "error": {"message": LIMIT}})])
        with self.assertRaises(StoryLoopError) as caught:
            _check_process({"contained": True, "returncode": 1, "stdout": stdout,
                            "stderr": "ERROR codex_models_manager: request timed out"})
        self.assertEqual(caught.exception.code, "AGENT_UNAVAILABLE")
        self.assertIn("usage limit", str(caught.exception))
        self.assertEqual(str(caught.exception).count("usage limit"), 1)


if __name__ == "__main__":
    unittest.main()
