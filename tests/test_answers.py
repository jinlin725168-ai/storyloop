"""Questions raised by agents are answered on the record, and the answers reach the next attempt."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.answers import load_answers, pending_questions, record_answers
from storyloop.cli import run_with_answers
from storyloop.models import StoryLoopError
from storyloop.project_loop import LoopServices, load_run_state, run_change
from tests.test_project_loop import smoke_payload
from tests.test_workspace_scratch import _approved as _historical_approved
from tests.routing_support import legacy_create_change

NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


def _approved(root, settings):
    """These answer-flow scenarios predate routing; keep their historical contracts explicit."""
    with patch('tests.test_workspace_scratch.create_change', legacy_create_change):
        return _historical_approved(root, settings)


def asking_services(change_id: str, seen: list) -> LoopServices:
    def implement(project, context):
        seen.append(context.get("answers", []))
        target = project / "modules/orders/service.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("VALUE = 1\n", encoding="utf-8")
        if not context.get("answers"):
            return {"summary": "need a decision", "questions": ["Sort by code or by name?"]}
        return {"summary": "done", "questions": []}

    def review(project, context):
        seen.append(("review", context.get("answers", [])))
        return {"verdict": "pass", "summary": "ok", "findings": [], "questions": []}

    return LoopServices(generate_smoke=lambda project, state: smoke_payload(change_id),
                        implement=implement, review=review)


class AnswersTests(unittest.TestCase):
    def test_answers_are_required_recorded_and_forwarded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = _approved(root, {})
            seen: list = []
            paused = run_change(root, change_id, asking_services(change_id, seen))
            self.assertEqual(paused["status"], "NEEDS_INPUT")
            self.assertEqual(pending_questions(root, change_id), ["Sort by code or by name?"])

            with self.assertRaises(StoryLoopError):
                run_change(root, change_id, asking_services(change_id, seen))

            recorded = record_answers(root, change_id, ["By code, ascending."], now=lambda: NOW)
            self.assertEqual(recorded["status"], "ANSWERED")
            self.assertEqual(pending_questions(root, change_id), [])
            entry = load_answers(root, change_id)[0]
            self.assertEqual((entry["attempt"], entry["question"], entry["answer"]),
                             (1, "Sort by code or by name?", "By code, ascending."))
            self.assertEqual(entry["answered_at"], "2026-09-19T00:00:00+00:00")

            done = run_change(root, change_id, asking_services(change_id, seen))
            self.assertEqual(done["status"], "PASSED", done)
            implement_contexts = [item for item in seen if not isinstance(item, tuple)]
            self.assertEqual(implement_contexts[-1][0]["answer"], "By code, ascending.")
            self.assertEqual(seen[-1][1][0]["question"], "Sort by code or by name?")
            self.assertEqual(load_run_state(root, change_id)["answers"][0]["answer"], "By code, ascending.")

    def test_record_answers_validates_input(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = _approved(root, {})
            with self.assertRaises(StoryLoopError):
                record_answers(root, change_id, ["nothing pending"])
            run_change(root, change_id, asking_services(change_id, []))
            with self.assertRaises(StoryLoopError):
                record_answers(root, change_id, ["one", "two"])  # one question, two answers
            with self.assertRaises(StoryLoopError):
                record_answers(root, change_id, [""])

    def test_interactive_run_prompts_records_and_continues(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = _approved(root, {})
            services = asking_services(change_id, [])
            prompts: list[str] = []

            def fake_input(prompt: str = "") -> str:
                prompts.append(prompt)
                return "By name."

            with patch("builtins.input", fake_input), patch("sys.stdin.isatty", return_value=True), \
                    patch("sys.stdout.isatty", return_value=True):
                result = run_with_answers(root, change_id, interactive=True,
                                          runner=lambda project, change: run_change(project, change, services))
            self.assertEqual(result["status"], "PASSED", result)
            self.assertTrue(any("Sort by code" in item for item in prompts))
            self.assertEqual(load_answers(root, change_id)[0]["answer"], "By name.")

    def test_non_interactive_run_returns_needs_input(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = _approved(root, {})
            services = asking_services(change_id, [])
            with patch("sys.stdin.isatty", return_value=False):
                result = run_with_answers(root, change_id, interactive=True,
                                          runner=lambda project, change: run_change(project, change, services))
            self.assertEqual(result["status"], "NEEDS_INPUT")


if __name__ == "__main__":
    unittest.main()
