"""Base implementation has a disposable execution root and the original control plane."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from storyloop.agent_transport import _codex, _command
from storyloop.baseline import load_project_config
from storyloop.models import StoryLoopError
from storyloop.project_agents import invoke_project_agent
from storyloop.storage import write_json
from tests import test_base_agent_flow as flow


class BaseAgentTransportTests(unittest.TestCase):
    setUp = flow.BaseAgentFlowTests.setUp

    def test_control_plane_stays_in_project_and_request_uses_worktree(self):
        config = load_project_config(self.root)
        config.update({"adapters": {"one": {"kind": "manual"}},
                       "roles": {"default_adapter": "one"}})
        write_json(self.root / ".storyloop/config.json", config)
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            with mock.patch("storyloop.project_agents.invoke_transport", return_value={}) as invoke:
                invoke_project_agent(self.root, "implement",
                                     {"mode": "base-implement", "change": self.change},
                                     workspace=workspace)
            self.assertTrue(Path(invoke.call_args.args[0]["project"]).samefile(workspace))
            self.assertTrue((self.root / ".storyloop/state/telemetry/calls").exists())
            self.assertFalse((workspace / ".storyloop").exists())

    def test_codex_base_schema_failure_does_not_resume_outside_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            control = workspace / "control"
            control.mkdir()
            (control / ".storyloop").mkdir()
            checkout = workspace / "checkout"
            checkout.mkdir()
            trace = {"instructions": "implement", "codex_instructions": "",
                     "codex_model": None, "codex_profile": {}, "sessions": [],
                     "control_project": str(control)}
            request = {"role": "implement", "project": str(checkout),
                       "payload": {"mode": "base-implement"}, "response_schema": {"type": "object"}}

            def invalid(_project, argv, _prompt, _timeout, telemetry, _trace):
                self.assertIn(str(checkout), argv)
                self.assertIn("workspace-write", argv)
                telemetry["thread"] = "session-id"
                return "not json"

            with mock.patch("storyloop.agent_transport._codex_executable", return_value=["codex"]), \
                 mock.patch("storyloop.agent_transport._codex_once", side_effect=invalid) as call:
                with self.assertRaises(StoryLoopError):
                    _codex(checkout, request, {"agent_timeout_seconds": 1}, {"type": "object"}, trace)
            self.assertEqual(call.call_count, 1)

    def test_command_adapter_uses_worktree_for_cwd_and_project_placeholder(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            response = {"contained": True, "returncode": 0, "stdout": "{}", "stderr": ""}
            with mock.patch("storyloop.agent_transport.run_process", return_value=response) as run:
                _command(workspace, {"project": str(workspace), "role": "implement"},
                         {"agent_command": [sys.executable, "{project}"],
                          "agent_timeout_seconds": 1},
                         {"type": "object", "properties": {}, "required": []},
                         {"instructions": "implement"})
            self.assertEqual(run.call_args.args[0][1], str(workspace))
            self.assertEqual(run.call_args.args[1], workspace)
