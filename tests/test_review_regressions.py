"""Regression tests for independently found trust/budget/resume failures."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tests.support import config, check, write
from storyloop import agents
from storyloop.models import StoryLoopError
from storyloop.storage import initialize, approval_digest
from storyloop.verification import run_check


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "case"
        initialize(self.project)
        self.config = config()

    def test_qa_source_in_build_named_directory_is_still_frozen(self):
        directory = self.project / "qa" / "target"
        directory.mkdir()
        script = directory / "check.py"
        script.write_text("assert False")
        before = approval_digest(self.project)
        script.write_text("assert True")
        self.assertNotEqual(before, approval_digest(self.project))

    def test_adapter_obeys_remaining_story_time(self):
        self.config.update(adapter="command", agent_command=[sys.executable, "-c", "print('{}')"])
        write(self.project / "config.json", self.config)
        result = {"returncode": 0, "stdout": "{}", "timed_out": False, "truncated": False, "error": ""}
        with patch("storyloop.agents.run_process", return_value=result) as process:
            agents.invoke(self.project, "review", {"timeout_seconds": 0.2})
            self.assertLessEqual(process.call_args.args[2], 0.2)

    def test_manual_response_survives_clock_and_attempt_bookkeeping(self):
        self.config["adapter"] = "manual"
        write(self.project / "config.json", self.config)
        with self.assertRaises(StoryLoopError):
            agents.invoke(self.project, "implement", {"attempt": 1, "timeout_seconds": 10, "checks": [{"seconds": 0.12}]})
        request = json.loads((self.project / ".storyloop/requests/implement.json").read_text())
        response = self.project / ".storyloop/responses/implement.json"
        response.parent.mkdir()
        write(response, {"request_id": request["request_id"], "result": {"summary": "ready", "questions": []}})
        result = agents.invoke(self.project, "implement", {"attempt": 2, "timeout_seconds": 9.3, "checks": [{"seconds": 0.34}]})
        self.assertEqual(result["summary"], "ready")

    def test_malformed_result_status_fails_closed(self):
        value = check()
        body = {"protocol": "storyloop.check.v1", "results": [{"id": "overlap", "status": [], "message": "bad"}]}
        value["command"][-1] = "print(" + repr(json.dumps(body)) + ")"
        self.assertEqual(run_check(self.project, value)["outcome"], "BLOCKED")


    def test_manual_review_response_is_bound_to_implementation_version(self):
        self.config["adapter"] = "manual"
        write(self.project / "config.json", self.config)
        with self.assertRaises(StoryLoopError):
            agents.invoke(self.project, "review", {"story": "same approved story"})
        request = json.loads((self.project / ".storyloop/requests/review.json").read_text())
        response = self.project / ".storyloop/responses/review.json"
        response.parent.mkdir()
        write(response, {"request_id": request["request_id"], "result": {"verdict": "pass"}})
        (self.project / "workspace/new.py").write_text("version = 2")
        with self.assertRaises(StoryLoopError):
            agents.invoke(self.project, "review", {"story": "same approved story"})


if __name__ == "__main__":
    unittest.main()
