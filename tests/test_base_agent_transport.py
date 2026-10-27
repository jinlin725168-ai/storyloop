"""Base implementation has a disposable execution root and the original control plane."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from storyloop.agent_transport import _codex, _codex_once, _command, _manual
from storyloop.base_agent_execution import _prepare_agent_call
from storyloop.base_agent_worktree import remove_agent_worktree
from storyloop.baseline import load_project_config
from storyloop.git_command import git
from storyloop.models import StoryLoopError
from storyloop.project_agents import invoke_project_agent
from storyloop.storage import load_json, write_json
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

    def test_codex_response_file_stays_in_original_control_plane(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            control, checkout = root / "control", root / "checkout"
            (control / ".storyloop").mkdir(parents=True)
            checkout.mkdir()
            trace = {"control_project": str(control), "sessions": [], "stderr": ""}
            telemetry = {"segments": 0, "turns": 0, "input_tokens": 0, "output_tokens": 0}

            def respond(argv, cwd, _timeout, **_kwargs):
                response = Path(argv[argv.index("--output-last-message") + 1])
                self.assertTrue(response.parent.samefile(control / ".storyloop"))
                self.assertTrue(cwd.samefile(checkout))
                response.write_text("{}", encoding="utf-8")
                return {"contained": True, "returncode": 0, "stdout": "", "stderr": ""}

            with mock.patch("storyloop.agent_transport.run_process", side_effect=respond):
                self.assertEqual(_codex_once(checkout, ["codex", "exec"], "prompt", 1,
                                             telemetry, trace), "{}")
            self.assertFalse((checkout / ".storyloop").exists())

    def test_manual_request_and_response_stay_in_original_control_plane(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            control, checkout = root / "control", root / "checkout"
            (control / ".storyloop").mkdir(parents=True)
            checkout.mkdir()
            request = {"role": "implement", "project": str(checkout),
                       "payload": {"mode": "base-implement"}}
            trace = {"control_project": str(control), "instructions": "implement"}
            with self.assertRaises(StoryLoopError) as caught:
                _manual(checkout, request, {"type": "object"}, trace)
            self.assertEqual(caught.exception.code, "NEEDS_INPUT")
            request_path = control / ".storyloop/requests/project-implement.json"
            self.assertTrue(request_path.exists())
            response_path = control / ".storyloop/responses/project-implement.json"
            write_json(response_path, {"request_id": load_json(request_path)["request_id"],
                                       "result": {"ok": True}})
            self.assertEqual(_manual(checkout, request, {"type": "object"}, trace), {"ok": True})
            self.assertFalse(response_path.exists())
            self.assertFalse((checkout / ".storyloop").exists())

    def test_manual_base_adapter_is_rejected_before_creating_checkout(self):
        config = load_project_config(self.root)
        config.update({"adapters": {"one": {"kind": "manual"}},
                       "roles": {"default_adapter": "one"}})
        write_json(self.root / ".storyloop/config.json", config)
        state = {"change_id": self.change["change_id"],
                 "contract_commit": git(self.root, "rev-parse", "HEAD").strip(),
                 "engine_source": str(self.root)}
        try:
            with mock.patch("storyloop.adapter_capabilities.ensure_checked"):
                with self.assertRaisesRegex(StoryLoopError, "--implementer human"):
                    _prepare_agent_call(self.root, self.change, state)
        finally:
            remove_agent_worktree(self.root, state)
        self.assertNotIn("agent_worktree", state)
