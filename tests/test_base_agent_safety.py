"""Agent base runs cannot judge changes to their own engine."""

import unittest
import os
from unittest import mock

from storyloop import base_execution, base_run_open, base_upgrade
from storyloop.approvals import approve_change
from storyloop.models import StoryLoopError
from storyloop.project_loop import _new_run_state, run_change
from storyloop.run_services import LoopServices
from storyloop.run_state import run_path
from storyloop.storage import load_json
from storyloop.base_design import approve_base_design
from tests import test_base_agent_flow as flow
from tests import test_base_lifecycle as lifecycle


class BaseAgentSafetyTests(unittest.TestCase):
    setUp = lifecycle.BaseG1Tests.setUp
    _services = flow.BaseAgentFlowTests._services

    def _approved(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, services=self._services())
        state = load_json(run_path(self.root, change_id))
        approve_base_design(self.root, change_id, state["base_design"]["plan_id"])
        run_change(self.root, change_id, services=self._services())

    def _implement(self, path, content="changed\n"):
        self._approved()
        services = self._services()

        def write(project, _payload):
            target = project / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return {"summary": "updated"}

        configured = LoopServices(services.generate_smoke, write, services.review,
                                  design=services.design)
        return run_change(self.root, self.change["change_id"], services=configured)

    def test_agent_rejects_unapproved_path(self):
        result = self._implement("extra.txt")
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")

    def test_agent_rejects_criterion_path(self):
        result = self._implement("acceptance/storyloop-settings.json")
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")

    def test_agent_rejects_report_path(self):
        result = self._implement(f"reports/storyloop/{self.change['change_id']}/report.json")
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")

    def test_agent_rejects_control_plane_file(self):
        result = self._implement(".storyloop/config.json", "{}\n")
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")
        resumed = run_change(self.root, self.change["change_id"], services=self._services())
        self.assertEqual(resumed["status"], "WORKSPACE_TAINTED")
        self.assertIn("restore", resumed["next"].lower())

    def test_agent_rejects_ci_path(self):
        result = self._implement(".github/workflows/ci.yml")
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")

    def test_agent_rejects_symlink_at_approved_path(self):
        self._approved()
        services = self._services()

        def link(project, _payload):
            (project / "pom.xml").unlink()
            os.symlink("README.md", project / "pom.xml")
            return {"summary": "linked"}

        configured = LoopServices(services.generate_smoke, link, services.review,
                                  design=services.design)
        result = run_change(self.root, self.change["change_id"], services=configured)
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")

    def test_agent_cannot_stage_a_rename(self):
        from storyloop.baseline import _git
        self._approved()
        services = self._services()

        def rename(project, _payload):
            _git(project, "mv", "pom.xml", "renamed.xml")
            return {"summary": "renamed"}

        configured = LoopServices(services.generate_smoke, rename, services.review,
                                  design=services.design)
        result = run_change(self.root, self.change["change_id"], services=configured)
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")

    def test_agent_self_hosting_is_rejected_before_branch_creation(self):
        proposed = base_upgrade.propose_base_change(
            self.root, ["storyloop/run_guards.py"], "调整引擎守卫")
        change = approve_change(self.root, proposed["change_id"])
        with mock.patch.object(base_run_open, "__file__", str(self.root / "storyloop/base_run_open.py")):
            with self.assertRaisesRegex(StoryLoopError, "self.host|T6|自举"):
                base_execution._open_state(self.root, change, "agent", _new_run_state)


if __name__ == "__main__":
    unittest.main()
