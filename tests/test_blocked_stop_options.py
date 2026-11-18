"""BLOCKED stops are saved with recovery options and bound to the run status they came from."""

from pathlib import Path
import tempfile
import unittest

from storyloop.errors import StoryLoopError
from storyloop.stop_report import build_stop, stop_of


def _questions(project, context):
    return {"summary": "ask", "touched": [], "tests": [], "claims": [],
            "questions": ["Which behavior should be locked?"]}


class BlockedStopOptions(unittest.TestCase):
    def test_built_blocked_stop_lists_log_then_run(self):
        state = {"change_id": "CH-1", "status": "CHARACTERIZING"}
        stop = build_stop("/p", state, "BLOCKED", {"error": "Engine said no"})
        self.assertEqual([o["command"] for o in stop["options"]],
                         ["storyloop log /p --change CH-1", "storyloop run /p --change CH-1"])
        self.assertTrue(all(o["action"].strip() for o in stop["options"]))
        self.assertEqual(stop["resume"], "storyloop run /p --change CH-1")
        self.assertEqual(stop["blocker"], "Engine said no")
        self.assertEqual(stop["class"], "other")

    def test_stop_of_returns_saved_blocked_stop_only_while_status_matches(self):
        saved = {"status": "BLOCKED", "options": []}
        run = {"change_id": "CH-1", "status": "CHARACTERIZING", "stop": saved,
               "blocked_from": "CHARACTERIZING"}
        self.assertIs(stop_of("/p", run), saved)
        self.assertIsNone(stop_of("/p", {**run, "status": "IMPLEMENTING"}))
        self.assertIsNone(stop_of("/p", {**run, "blocked_from": None}))

    def test_blocked_run_saves_stop_keeps_status_and_rerun_clears_it(self):
        from storyloop.testing.nested import characterize, nested_case
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            case = nested_case(root, "standard", characterize=_questions)
            with self.assertRaises(StoryLoopError) as caught:
                case.run()
            self.assertEqual(caught.exception.code, "BLOCKED")
            state = case.state()
            stop = state.get("stop")
            self.assertIsInstance(stop, dict)
            self.assertEqual(stop["status"], "BLOCKED")
            self.assertEqual(stop["blocker"], str(caught.exception))
            self.assertEqual(state["blocked_from"], state["status"])
            self.assertNotEqual(state["status"], "BLOCKED")
            self.assertEqual(stop_of(root, state), stop)
            self.assertEqual(case.run(characterize=characterize)["status"], "PASSED")
            after = case.state()
            self.assertNotIn("blocked_from", after)
            self.assertNotEqual((after.get("stop") or {}).get("status"), "BLOCKED")


if __name__ == "__main__":
    unittest.main()
