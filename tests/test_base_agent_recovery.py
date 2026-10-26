"""Premerge agent design files are never accepted from an interrupted write."""

import unittest
from unittest import mock

from storyloop.project_loop import run_change
from storyloop.run_state import run_path
from storyloop.storage import load_json, write_json
from tests import test_base_agent_flow as flow


class BaseAgentRecoveryTests(unittest.TestCase):
    setUp = flow.BaseAgentFlowTests.setUp
    _services = flow.BaseAgentFlowTests._services

    def test_plan_temp_interruption_restarts(self):
        change_id = self.change["change_id"]
        self.assertEqual(run_change(self.root, change_id, services=self._services())["status"],
                         "DESIGN_APPROVAL_REQUIRED")
        path = run_path(self.root, change_id)
        state = load_json(path)
        state["status"] = "IMPLEMENTING"
        state["base_inflight"] = "base_design"
        write_json(path, state)
        temp = self.root / ".storyloop/state/base-designs" / f"{change_id}.json.abcd.tmp"
        temp.write_text("incomplete", encoding="utf-8")
        self.assertEqual(run_change(self.root, change_id, services=self._services())["status"],
                         "CONTRACT_COMMITTED")
        self.assertFalse(temp.exists())

    def test_plan_write_before_state_restarts(self):
        change_id = self.change["change_id"]
        self.assertEqual(run_change(self.root, change_id, services=self._services())["status"],
                         "DESIGN_APPROVAL_REQUIRED")
        path = run_path(self.root, change_id)
        state = load_json(path)
        state["status"] = "IMPLEMENTING"
        state["base_inflight"] = "base_design"
        state.pop("base_design_sha256")
        write_json(path, state)
        self.assertEqual(run_change(self.root, change_id, services=self._services())["status"],
                         "CONTRACT_COMMITTED")
        self.assertFalse((self.root / ".storyloop/state/base-designs" / f"{change_id}.json").exists())

    def test_plan_approval_before_state_restarts(self):
        change_id = self.change["change_id"]
        self.assertEqual(run_change(self.root, change_id, services=self._services())["status"],
                         "DESIGN_APPROVAL_REQUIRED")
        state = load_json(run_path(self.root, change_id))
        from storyloop import base_design
        original = base_design.save_run

        def interrupt(project, candidate):
            if candidate.get("base_design_approval_sha256"):
                raise RuntimeError("crash before approval state save")
            return original(project, candidate)

        with mock.patch.object(base_design, "save_run", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "crash before approval"):
                base_design.approve_base_design(self.root, change_id, state["base_design"]["plan_id"])
        self.assertEqual(run_change(self.root, change_id, services=self._services())["status"],
                         "CONTRACT_COMMITTED")

