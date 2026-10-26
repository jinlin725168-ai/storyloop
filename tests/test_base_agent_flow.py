"""The selected base implementer is frozen in a real run."""

import unittest

from storyloop import base_execution, base_upgrade
from storyloop.approvals import approve_change
from storyloop.baseline import load_project_config
from storyloop.models import StoryLoopError
from storyloop.project_agents import invoke_project_agent
from storyloop.project_loop import _new_run_state, run_change
from storyloop.run_services import LoopServices
from storyloop.run_state import run_path
from storyloop.storage import load_json, write_json
from tests import test_base_lifecycle as lifecycle


class BaseAgentFlowTests(unittest.TestCase):
    setUp = lifecycle.BaseG1Tests.setUp

    def _services(self):
        return LoopServices(
            generate_smoke=lambda *_: {}, implement=lambda *_: {}, review=lambda *_: {},
            design=lambda *_: {"summary": "更新构建配置", "steps": ["编辑 pom.xml"],
                               "risks": ["构建可能失败"], "rollback": "恢复原文件", "questions": []},
        )

    def test_foundation_defaults_to_agent_without_manual_flag(self):
        state = base_execution._open_state(self.root, self.change, None, _new_run_state)
        self.assertEqual(state["implementer"], "agent")

    def test_agent_run_completes_g1_and_waits_for_design_approval(self):
        result = run_change(self.root, self.change["change_id"], services=self._services())
        self.assertEqual(result["status"], "DESIGN_APPROVAL_REQUIRED")
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

    def test_agent_plan_requires_named_approval_before_implementation(self):
        change_id = self.change["change_id"]
        result = run_change(self.root, change_id, services=self._services())
        self.assertEqual(result["status"], "DESIGN_APPROVAL_REQUIRED")
        state = load_json(run_path(self.root, change_id))
        self.assertEqual(state["base_design"]["paths"], ["pom.xml"])
        self.assertEqual(run_change(self.root, change_id, services=self._services())["status"],
                         "DESIGN_APPROVAL_REQUIRED")
        from storyloop.base_design import approve_base_design
        approved = approve_base_design(self.root, change_id, state["base_design"]["plan_id"])
        self.assertEqual(approved["actor"], "StoryLoop Test")
        self.assertEqual(run_change(self.root, change_id, services=self._services())["status"],
                         "IMPLEMENTING")

    def test_human_run_does_not_call_design_agent(self):
        services = self._services()
        services = LoopServices(services.generate_smoke, services.implement, services.review,
                                design=lambda *_: self.fail("human path called design"))
        result = run_change(self.root, self.change["change_id"], services=services,
                            implementer="human")
        self.assertEqual(result["status"], "HUMAN_IMPLEMENTATION_REQUIRED")

    def test_modified_approved_plan_discards_the_entire_attempt(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, services=self._services())
        from storyloop.base_design import approve_base_design
        state = load_json(run_path(self.root, change_id))
        approve_base_design(self.root, change_id, state["base_design"]["plan_id"])
        plan_path = self.root / ".storyloop/state/base-designs" / f"{change_id}.json"
        plan = load_json(plan_path)
        plan["steps"] = ["偷换步骤"]
        write_json(plan_path, plan)
        self.assertEqual(run_change(self.root, change_id, services=self._services())["status"],
                         "CONTRACT_COMMITTED")
        self.assertFalse(plan_path.exists())

    def test_design_approval_rejects_code_written_before_human_decision(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, services=self._services())
        from storyloop.base_design import approve_base_design
        state = load_json(run_path(self.root, change_id))
        (self.root / "pom.xml").write_text("<project><version>2</version></project>\n",
                                            encoding="utf-8")
        with self.assertRaisesRegex(StoryLoopError, "clean|candidate|before approval"):
            approve_base_design(self.root, change_id, state["base_design"]["plan_id"])

    def test_approved_agent_writes_candidate_and_reaches_review(self):
        from storyloop.base_design import approve_base_design
        change_id = self.change["change_id"]
        run_change(self.root, change_id, services=self._services())
        state = load_json(run_path(self.root, change_id))
        approve_base_design(self.root, change_id, state["base_design"]["plan_id"])
        self.assertEqual(run_change(self.root, change_id, services=self._services())["status"],
                         "IMPLEMENTING")

        def implement(project, _payload):
            (project / "pom.xml").write_text("<project><version>2</version></project>\n",
                                               encoding="utf-8")
            return {"summary": "updated"}

        services = self._services()
        services = LoopServices(services.generate_smoke, implement, services.review,
                                design=services.design)
        result = run_change(self.root, change_id, services=services)
        self.assertEqual(result["status"], "BASE_REVIEW_REQUIRED")
        saved = load_json(run_path(self.root, change_id))
        self.assertNotEqual(saved["candidate_commit"], saved["agent_call_head"])
        self.assertEqual(saved["agent_call_head"], saved["contract_commit"])
        self.assertTrue(saved["base_evidence_sha256"])


if __name__ == "__main__":
    unittest.main()
