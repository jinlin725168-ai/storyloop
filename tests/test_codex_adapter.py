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
from storyloop.telemetry import summarize, ledger_entries, record_call
from storyloop.storage import load_json, write_json
from storyloop.adapter_capabilities import check
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


class CodexProfileTests(unittest.TestCase):
    def test_requested_model_reaches_cli_and_ledger(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project(root)
            config = load_json(root / '.storyloop/config.json')
            config.update(adapters={'primary': {'kind': 'codex', 'models': {
                'strong': 'mapped-strong', 'standard': 'mapped-standard'}}}, roles={'default_adapter': 'primary'})
            write_json(root / '.storyloop/config.json', config)
            fake = FakeCodex(['{"summary":"done","questions":[]}'])
            with patch('storyloop.project_agents.run_process', fake), \
                    patch('storyloop.project_agents._codex_executable', return_value=['codex']):
                invoke_project_agent(root, 'implement', {'change_id': 'FEAT-1', 'routing_tier': 'light'})
            argv = fake.calls[0][0]
            self.assertEqual(argv[argv.index('-m') + 1], 'mapped-standard')
            entry = ledger_entries(root, 'FEAT-1')[0]
            self.assertEqual(entry['tier_requested'], 'standard')
            self.assertEqual(entry['tier_applied'], 'standard')
            self.assertEqual(entry['model'], 'mapped-standard')
            self.assertEqual(entry['vendor'], 'openai')
            self.assertEqual(entry['adapter'], 'primary')
            self.assertEqual(entry['path_guard'], 'unsupported')
            self.assertEqual(entry['subagents_applied']['mode'], 'sequential')

    def test_missing_configured_tier_rejected_before_cli(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project(root)
            config = load_json(root / '.storyloop/config.json')
            config.update(adapters={'primary': {'kind': 'codex', 'models': {'standard': 'small'}}},
                          roles={'default_adapter': 'primary'})
            write_json(root / '.storyloop/config.json', config)
            with self.assertRaises(StoryLoopError) as caught:
                check(root)
            self.assertEqual(caught.exception.code, 'ADAPTER_PROFILE_UNSUPPORTED')

    def test_unconfigured_models_do_not_claim_applied_tier(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project(root)
            config = load_json(root / '.storyloop/config.json')
            config.update(adapters={'primary': {'kind': 'codex'}}, roles={'default_adapter': 'primary'})
            write_json(root / '.storyloop/config.json', config)
            fake = FakeCodex(['{"summary":"done","questions":[]}'])
            with patch('storyloop.project_agents.run_process', fake), \
                    patch('storyloop.project_agents._codex_executable', return_value=['codex']):
                invoke_project_agent(root, 'implement', {'change_id': 'FEAT-2'})
            self.assertNotIn('-m', fake.calls[0][0])
            entry = ledger_entries(root, 'FEAT-2')[0]
            self.assertNotIn('tier_applied', entry)
            self.assertNotIn('model', entry)
            self.assertEqual(entry['vendor'], 'openai')


class AdapterDiagnosticTests(unittest.TestCase):
    def test_diagnostics_reject_malformed_claims_and_cannot_spoof_engine_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for count in (-1, True, 1.5, 10001, '1'):
                telemetry = {'path_guard': 'unsupported', 'adapter': 'spoof', 'tier_requested': 'spoof',
                    'subagents_applied': {'mode': 'sequential', 'perspectives': count, 'explore': 0, 'lanes': 0}}
                entry = record_call(root, 'review', 'FEAT-1', telemetry, 1)
                self.assertNotIn('subagents_applied', entry)
                self.assertNotIn('adapter', entry)
                self.assertNotIn('tier_requested', entry)
                self.assertEqual(entry['path_guard'], 'unsupported')
            telemetry = {'path_guard': ['enforced'], 'subagents_applied': {'mode': ['parallel']}}
            entry = record_call(root, 'review', 'FEAT-1', telemetry, 1)
            self.assertNotIn('path_guard', entry)
            self.assertNotIn('subagents_applied', entry)


if __name__ == "__main__":
    unittest.main()
