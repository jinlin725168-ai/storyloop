"""The same failure twice leads to root-cause mode and then NO_PROGRESS (subproject 2a §5)."""

from pathlib import Path
import tempfile
import unittest

from storyloop.acceptance_repair import repair_smoke
from storyloop.answers import record_answers
from storyloop.models import StoryLoopError
from storyloop.project_loop import LoopServices, load_run_state, run_change
from tests.test_failure_routing import approved, failing_review


def two_case_suite(change_id: str) -> dict:
    def check(name):
        return f"from pathlib import Path\nraise SystemExit(0 if Path('modules/orders/{name}.py').is_file() else 1)\n"
    return {"files": [{"path": "a.py", "content": check("a")}, {"path": "b.py", "content": check("b")}],
            "manifest": {"version": 1, "tests": [
                {"id": "S-A", "criteria": ["AC-1"], "command": ["{python}", f"acceptance/changes/{change_id}/a.py"], "timeout_seconds": 10},
                {"id": "S-B", "criteria": ["AC-1"], "command": ["{python}", f"acceptance/changes/{change_id}/b.py"], "timeout_seconds": 10}]}}


class NoProgressTests(unittest.TestCase):
    def _question_breaks_streak(self, question_attempt):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root, max_attempts=6)
            calls = []

            def implement(project, context):
                calls.append((context["attempt"], context.get("mode")))
                questions = ["按原要求继续吗？"] if context["attempt"] == question_attempt else []
                return {"summary": "等待澄清" if questions else "尝试实现", "questions": questions}

            services = LoopServices(generate_smoke=lambda p, s: two_case_suite(change_id),
                                    implement=implement, review=failing_review())
            self.assertEqual(run_change(root, change_id, services)["status"], "NEEDS_INPUT")
            record_answers(root, change_id, ["继续"])
            self.assertEqual(run_change(root, change_id, services)["status"], "NO_PROGRESS")
            resumed = calls[question_attempt:]
            self.assertEqual(resumed, [(question_attempt + 1, None),
                                       (question_attempt + 2, None),
                                       (question_attempt + 3, "root_cause")])
            state = load_run_state(root, change_id)
            self.assertEqual(state["attempt"], question_attempt + 3)

    def test_needs_input_between_failures_breaks_the_streak(self):
        self._question_breaks_streak(2)

    def test_needs_input_after_two_failures_resets_the_next_mode(self):
        self._question_breaks_streak(3)

    def test_an_unchanged_root_cause_attempt_stops_before_the_budget_is_spent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root, max_attempts=6)
            modes = []

            def implement(project, context):
                modes.append(context.get("mode"))
                (project / "modules/orders").mkdir(parents=True, exist_ok=True)
                (project / "modules/orders/note.py").write_text("X = 1\n", encoding="utf-8")
                return {"summary": "tried", "questions": []}

            result = run_change(root, change_id, LoopServices(
                generate_smoke=lambda p, s: two_case_suite(change_id), implement=implement,
                review=failing_review()))
            self.assertEqual(result["status"], "NO_PROGRESS")
            self.assertEqual(modes, [None, None, "root_cause"])
            self.assertEqual(load_run_state(root, change_id)["attempt"], 3)
            repaired = two_case_suite(change_id)
            repaired["files"][0]["content"] += "# repaired\n"
            repair = repair_smoke(root, change_id, "the oracle also needs repair", LoopServices(
                generate_smoke=lambda p, s: repaired, implement=implement, review=failing_review()))
            self.assertEqual(repair["status"], "ACCEPTANCE_REPAIRED")
            state = load_run_state(root, change_id)
            self.assertEqual(state["attempt"], 0)
            self.assertNotIn("mode", state)

    def test_a_case_turning_green_counts_as_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root, max_attempts=4)
            modes = []

            def implement(project, context):
                modes.append(context.get("mode"))
                (project / "modules/orders").mkdir(parents=True, exist_ok=True)
                if context.get("mode") == "root_cause":
                    (project / "modules/orders/a.py").write_text("A = 1\n", encoding="utf-8")
                return {"summary": "tried", "questions": []}

            result = run_change(root, change_id, LoopServices(
                generate_smoke=lambda p, s: two_case_suite(change_id), implement=implement,
                review=failing_review()))
            self.assertEqual(result["status"], "BUDGET_EXHAUSTED")
            self.assertEqual(modes, [None, None, "root_cause", None])

    def test_agent_unavailable_does_not_join_the_failure_streak(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root, max_attempts=4)
            calls = []

            def implement(project, context):
                calls.append(context.get("mode"))
                if len(calls) == 2:
                    raise StoryLoopError("offline", code="AGENT_UNAVAILABLE")
                (project / "modules/orders").mkdir(parents=True, exist_ok=True)
                return {"summary": "tried", "questions": []}

            services = LoopServices(generate_smoke=lambda p, s: two_case_suite(change_id),
                                    implement=implement, review=failing_review())
            with self.assertRaises(StoryLoopError):
                run_change(root, change_id, services)
            self.assertEqual(len(load_run_state(root, change_id)["failures"]), 1)
            run_change(root, change_id, services)
            self.assertEqual(calls[:4], [None, None, None, "root_cause"])


if __name__ == "__main__":
    unittest.main()
