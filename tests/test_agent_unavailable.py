"""An agent that never answered (crash, timeout, rate limit) is not a failed attempt: the quality budget
is spent only on answers the loop could judge."""

from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop import cli
from storyloop.baseline import load_project_config
from storyloop.models import StoryLoopError
from storyloop.project_agents import invoke_project_agent
from storyloop.project_loop import LoopServices, load_run_state, run_change
from storyloop.storage import write_json
from tests.test_project_loop import smoke_payload
from tests.test_structured_output import approved_change, command_project, passing_review


def unavailable(message: str = "Project agent process failed safely (timed_out)") -> StoryLoopError:
    return StoryLoopError(message, code="AGENT_UNAVAILABLE")


def implement_after(failures: int, calls: list):
    """Raise AGENT_UNAVAILABLE for the first `failures` calls, then implement for real."""
    def implement(project, context):
        calls.append(context["attempt"])
        if len(calls) <= failures:
            raise unavailable()
        target = project / "modules/orders/service.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("VALUE = 1\n", encoding="utf-8")
        return {"summary": "done", "questions": []}
    return implement


def single_attempt(root: Path) -> None:
    config = load_project_config(root)
    config["max_attempts"] = 1
    write_json(root / ".storyloop/config.json", config)


class AdapterErrorCodeTests(unittest.TestCase):
    def test_a_command_agent_that_exits_nonzero_is_reported_as_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            command_project(root, "import sys; sys.stderr.write('rate limited\\n'); sys.exit(1)")
            with self.assertRaises(StoryLoopError) as raised:
                invoke_project_agent(root, "review", {"change_id": "FEAT-1"})
            self.assertEqual(raised.exception.code, "AGENT_UNAVAILABLE")


class LoopBudgetTests(unittest.TestCase):
    def test_an_implementer_that_never_answered_does_not_spend_the_only_attempt(self):
        temporary, root, change_id = approved_change()
        calls: list = []
        services = LoopServices(generate_smoke=lambda project, state: smoke_payload(change_id),
                                implement=implement_after(1, calls), review=passing_review)
        try:
            single_attempt(root)
            with self.assertRaises(StoryLoopError) as raised:
                run_change(root, change_id, services)
            self.assertEqual(raised.exception.code, "AGENT_UNAVAILABLE")
            state = load_run_state(root, change_id)
            self.assertEqual(state["attempt"], 0)  # the interrupted attempt is handed back
            self.assertEqual(state["status"], "IMPLEMENTING")
            self.assertEqual(state["agent_failures"][0]["attempt"], 1)
            self.assertEqual(state["agent_failures"][0]["role"], "implement")
            result = run_change(root, change_id, services)
            self.assertEqual(result["status"], "PASSED", result)
            self.assertEqual(calls, [1, 1])  # attempt 1 was retried, not skipped
        finally:
            temporary.cleanup()

    def test_a_reviewer_that_never_answered_does_not_spend_the_attempt_either(self):
        temporary, root, change_id = approved_change()
        reviews: list = []

        def review(project, context):
            reviews.append(context["attempt"])
            if len(reviews) == 1:
                raise unavailable()
            return passing_review(project, context)

        services = LoopServices(generate_smoke=lambda project, state: smoke_payload(change_id),
                                implement=implement_after(0, []), review=review)
        try:
            single_attempt(root)
            with self.assertRaises(StoryLoopError):
                run_change(root, change_id, services)
            self.assertEqual(load_run_state(root, change_id)["agent_failures"][0]["role"], "review")
            result = run_change(root, change_id, services)
            self.assertEqual(result["status"], "PASSED", result)
            self.assertEqual(reviews, [1, 1])
        finally:
            temporary.cleanup()

    def test_a_judged_failure_still_spends_the_attempt(self):
        """Only unanswered calls are handed back; a real verdict keeps costing the budget."""
        temporary, root, change_id = approved_change()
        services = LoopServices(generate_smoke=lambda project, state: smoke_payload(change_id),
                                implement=lambda project, context: {"summary": "nothing", "questions": []},
                                review=passing_review)
        try:
            single_attempt(root)
            self.assertEqual(run_change(root, change_id, services)["status"], "BUDGET_EXHAUSTED")
            self.assertNotIn("agent_failures", load_run_state(root, change_id))
        finally:
            temporary.cleanup()


class CliHintTests(unittest.TestCase):
    def test_the_error_exits_2_and_says_the_attempt_was_not_charged(self):
        with patch("storyloop.cli.dispatch", side_effect=unavailable()), \
                patch("sys.stdout", new_callable=io.StringIO), \
                patch("sys.stderr", new_callable=io.StringIO) as err:
            code = cli.main(["run", ".", "--change", "FEAT-1"])
        self.assertEqual(code, 2)
        error = json.loads(err.getvalue())
        self.assertEqual(error["status"], "AGENT_UNAVAILABLE")
        self.assertIn("不计入", error["next"])


if __name__ == "__main__":
    unittest.main()
