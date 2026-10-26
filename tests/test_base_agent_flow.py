"""The selected base implementer is frozen in a real run."""

import unittest

from storyloop import base_execution, base_upgrade
from storyloop.approvals import approve_change
from storyloop.baseline import load_project_config
from storyloop.models import StoryLoopError
from storyloop.project_agents import invoke_project_agent
from storyloop.project_loop import _new_run_state, run_change
from storyloop.run_state import run_path
from storyloop.storage import load_json, write_json
from tests import test_base_lifecycle as lifecycle


class BaseAgentFlowTests(unittest.TestCase):
    setUp = lifecycle.BaseG1Tests.setUp

    def test_foundation_defaults_to_agent_without_manual_flag(self):
        state = base_execution._open_state(self.root, self.change, None, _new_run_state)
        self.assertEqual(state["implementer"], "agent")

    def test_agent_run_completes_g1_without_entering_human_stop(self):
        result = run_change(self.root, self.change["change_id"])
        self.assertEqual(result["status"], "IMPLEMENTING")
        self.assertEqual(load_json(run_path(self.root, self.change["change_id"]))["implementer"], "agent")

    def test_ci_defaults_to_human_and_rejects_explicit_agent(self):
        proposed = base_upgrade.propose_base_change(
            self.root, [".github/workflows/ci.yml"], "调整 CI", allow_ci=True)
        change = approve_change(self.root, proposed["change_id"])
        with self.assertRaisesRegex(StoryLoopError, "CI|ci"):
            base_execution._open_state(self.root, change, "agent", _new_run_state)
        state = base_execution._open_state(self.root, change, None, _new_run_state)
        self.assertEqual(state["implementer"], "human")

    def test_upstream_defaults_to_human(self):
        proposed = base_upgrade.propose_base_change(
            self.root, ["modules/updated.py"], "同步上游", allow_mutable=True)
        change = approve_change(self.root, proposed["change_id"])
        state = base_execution._open_state(self.root, change, None, _new_run_state)
        self.assertEqual(state["implementer"], "human")

    def test_saved_e1_run_without_implementer_remains_readable(self):
        base_execution._open_state(self.root, self.change, "human", _new_run_state)
        path = run_path(self.root, self.change["change_id"])
        saved = load_json(path)
        saved.pop("implementer")
        write_json(path, saved)
        before = path.read_bytes()
        state = base_execution._open_state(self.root, self.change, None, _new_run_state)
        self.assertNotIn("implementer", state)
        self.assertEqual(path.read_bytes(), before)

    def test_saved_implementer_cannot_be_switched(self):
        base_execution._open_state(self.root, self.change, "human", _new_run_state)
        with self.assertRaisesRegex(StoryLoopError, "implementer|human"):
            base_execution._open_state(self.root, self.change, "agent", _new_run_state)

    def test_base_transport_request_uses_heavy_routing_and_strong_model(self):
        config = load_project_config(self.root)
        config.update({"adapters": {"one": {"kind": "manual"}},
                       "roles": {"default_adapter": "one"}})
        write_json(self.root / ".storyloop/config.json", config)
        with self.assertRaisesRegex(StoryLoopError, "Manual input"):
            invoke_project_agent(self.root, "implement", {"change": self.change})
        request = load_json(self.root / ".storyloop/requests/project-implement.json")
        self.assertEqual(request["role_profile"]["routing_tier"], "heavy")
        self.assertEqual(request["role_profile"]["tier"], "strong")


if __name__ == "__main__":
    unittest.main()
