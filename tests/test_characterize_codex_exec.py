"""Preserved Codex exec behavior before the resume argument fix."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.models import StoryLoopError
from storyloop.project_agents import invoke_project_agent
from storyloop.storage import load_json, write_json
from tests.test_codex_adapter import FakeCodex, project


class CodexExecCharacterization(unittest.TestCase):
    def test_first_exec_keeps_arguments_and_returns_without_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project(root)
            config_path = root / ".storyloop/config.json"
            config = load_json(config_path)
            config.update(adapters={"primary": {"kind": "codex", "models": {
                "strong": "mapped-strong", "standard": "mapped-standard"}}},
                roles={"default_adapter": "primary"})
            write_json(config_path, config)
            fake = FakeCodex(['{"summary":"done","questions":[]}'])

            with patch("storyloop.agent_transport.run_process", fake), \
                    patch("storyloop.agent_transport._codex_executable", return_value=["codex"]):
                result = invoke_project_agent(root, "implement", {
                    "change_id": "FEAT-CHAR-1", "routing_tier": "light"})

            argv = fake.calls[0][0]
            self.assertEqual(argv[:12], ["codex", "exec", "--sandbox", "workspace-write",
                                          "-C", str(root), "--ignore-user-config", "--color",
                                          "never", "--json", "-m", "mapped-standard"])
            self.assertIn("--output-schema", argv)
            self.assertIn("--output-last-message", argv)
            self.assertNotIn("resume", argv)
            self.assertEqual(len(fake.calls), 1)
            self.assertEqual(result["summary"], "done")
            self.assertIn("questions", result)

    def test_non_json_replies_allow_only_two_repairs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project(root)
            fake = FakeCodex(["prose", "still prose", "again"])

            with patch("storyloop.agent_transport.run_process", fake), \
                    patch("storyloop.agent_transport._codex_executable", return_value=["codex"]):
                with self.assertRaises(StoryLoopError):
                    invoke_project_agent(root, "review", {"change_id": "FEAT-CHAR-2"})

            self.assertEqual(len(fake.calls), 3)
            self.assertEqual(fake.calls[0][0][1], "exec")
            for command, _ in fake.calls[1:]:
                self.assertEqual(command[1:4], ["exec", "resume",
                                                "01a0bf27-ea11-7450-bb4a-7d83c21d63db"])


if __name__ == "__main__":
    unittest.main()
