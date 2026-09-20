"""The built-in Codex adapter keeps sessions resumable and repairs non-JSON final messages."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.baseline import bootstrap_project
from storyloop.models import StoryLoopError
from storyloop.project_agents import invoke_project_agent
from storyloop.telemetry import summarize
from tests.test_baseline_behavior import repository

THREAD = "01a0bf27-ea11-7450-bb4a-7d83c21d63db"


def events(text: str, turns: int = 1) -> str:
    lines = [json.dumps({"type": "thread.started", "thread_id": THREAD})]
    for _ in range(turns):
        lines.append(json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 7}}))
    lines.append(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}))
    return "\n".join(lines) + "\n"


class FakeCodex:
    """Answers each codex invocation in order, writing the last message like the CLI does."""

    def __init__(self, messages: list[str]):
        self.messages, self.calls = messages, []

    def __call__(self, command, cwd, timeout, input_text=None, **kwargs):
        self.calls.append((list(command), input_text))
        message = self.messages[len(self.calls) - 1]
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(message, encoding="utf-8")
        return {"returncode": 0, "stdout": events(message), "stderr": "", "timed_out": False,
                "truncated": False, "error": "", "contained": True}


def project(root: Path) -> None:
    repository(root)
    bootstrap_project(root)  # default adapter is codex


class CodexAdapterTests(unittest.TestCase):
    def test_sessions_persist_and_usage_is_metered(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project(root)
            fake = FakeCodex(['{"summary": "done", "questions": []}'])
            with patch("storyloop.project_agents.run_process", fake), \
                    patch("storyloop.project_agents._codex_executable", return_value=["codex"]):
                result = invoke_project_agent(root, "implement", {"change": {"change_id": "FEAT-1"}})
            self.assertEqual(result, {"summary": "done", "questions": []})
            command = fake.calls[0][0]
            self.assertNotIn("--ephemeral", command)
            self.assertIn("--json", command)
            summary = summarize(root, "FEAT-1")
            self.assertEqual(summary["calls"], 1)
            self.assertEqual(summary["by_role"]["implement"]["turns"], 1)
            self.assertIsNone(summary["cost_usd"])

    def test_non_json_final_message_is_repaired_by_resuming_the_thread(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project(root)
            fake = FakeCodex(["I finished the work, here is a summary in prose.",
                              '{"summary": "done", "questions": []}'])
            with patch("storyloop.project_agents.run_process", fake), \
                    patch("storyloop.project_agents._codex_executable", return_value=["codex"]):
                result = invoke_project_agent(root, "implement", {"change": {"change_id": "FEAT-2"}})
            self.assertEqual(result["summary"], "done")
            self.assertEqual(len(fake.calls), 2)
            resume = fake.calls[1][0]
            self.assertEqual(resume[1:3], ["exec", "resume"])
            self.assertEqual(resume[3], THREAD)
            self.assertIn("JSON", fake.calls[1][1])
            self.assertEqual(summarize(root, "FEAT-2")["by_role"]["implement"]["turns"], 2)

    def test_repair_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project(root)
            fake = FakeCodex(["prose", "still prose", "and again", "never json"])
            with patch("storyloop.project_agents.run_process", fake), \
                    patch("storyloop.project_agents._codex_executable", return_value=["codex"]), \
                    self.assertRaises(StoryLoopError):
                invoke_project_agent(root, "review", {"change": {"change_id": "FEAT-3"}})
            self.assertEqual(len(fake.calls), 3)  # one run plus two repairs


if __name__ == "__main__":
    unittest.main()
