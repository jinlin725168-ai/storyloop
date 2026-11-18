"""Characterize what must hold when BLOCKED stops gain recovery options."""

from pathlib import Path
import shlex
import tempfile
import unittest

from storyloop.errors import StoryLoopError
from storyloop.stop_report import build_stop, stop_of


def _questions(project, context):
    return {"summary": "ask", "touched": [], "tests": [], "claims": [],
            "questions": ["Which behavior should be locked?"]}


def _writes_extra(project, context):
    from storyloop.testing.nested import characterize
    (Path(project) / "app").mkdir(exist_ok=True)
    (Path(project) / "app/extra.py").write_text("X = 1\n", encoding="utf-8")
    return characterize(project, context)


class BlockedStopCharacterization(unittest.TestCase):
    def test_boundary_violation_stop_options_are_unchanged(self):
        from storyloop.testing.nested import nested_case
        with tempfile.TemporaryDirectory() as folder:
            # Resolved like the engine does: Windows temp paths may be 8.3 short names.
            root = Path(folder).resolve()
            case = nested_case(root, "standard", characterize=_writes_extra)
            case.run()
            stop = case.state()["stop"]
            self.assertEqual(stop["status"], "BOUNDARY_VIOLATION")
            commands = [item["command"] for item in stop["options"]]
            self.assertEqual(len(commands), 2)
            self.assertEqual(commands[0], shlex.join(["storyloop", "run", str(root), "--change", case.change_id]))
            self.assertTrue(commands[1].startswith(shlex.join(["storyloop", "base-propose", str(root), "--path"])))

    def test_rerun_after_a_blocked_error_reaches_passed(self):
        from storyloop.testing.nested import nested_case
        with tempfile.TemporaryDirectory() as folder:
            case = nested_case(Path(folder), "standard", characterize=_questions)
            with self.assertRaises(StoryLoopError) as caught:
                case.run()
            self.assertEqual(caught.exception.code, "BLOCKED")
            from storyloop.testing.nested import characterize
            self.assertEqual(case.run(characterize=characterize)["status"], "PASSED")

    def test_blocker_class_and_resume_of_a_built_blocked_stop(self):
        state = {"change_id": "CH-1", "status": "CHARACTERIZING"}
        stop = build_stop("/proj", state, "BLOCKED", {"error": "Engine said no"})
        self.assertEqual(stop["status"], "BLOCKED")
        self.assertEqual(stop["blocker"], "Engine said no")
        self.assertEqual(stop["class"], "other")
        self.assertEqual(stop["resume"], "storyloop run /proj --change CH-1")
        self.assertIsInstance(stop["options"], list)

    def test_other_stop_options_are_unchanged(self):
        state = {"change_id": "CH-1", "status": "X"}

        def commands(status):
            return [o["command"] for o in build_stop("/p", state, status, {})["options"]]

        self.assertEqual(commands("AGENT_UNAVAILABLE"), ["storyloop run /p --change CH-1"])
        self.assertEqual(commands("CHARACTERIZATION_FAILED"), ["storyloop run /p --change CH-1"])
        self.assertTrue(commands("NEEDS_INPUT")[0].startswith("storyloop answer /p --change CH-1 --answer"))

    def test_stop_of_is_none_without_saved_stop_for_non_display_status(self):
        self.assertIsNone(stop_of("/p", {"change_id": "CH-1", "status": "CHARACTERIZING"}))
        self.assertIsNone(stop_of("/p", None))


if __name__ == "__main__":
    unittest.main()
