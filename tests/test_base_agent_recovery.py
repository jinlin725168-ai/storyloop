"""Premerge agent design files are never accepted from an interrupted write."""

import unittest
from unittest import mock

from storyloop.baseline import _git
from storyloop.models import StoryLoopError
from storyloop.project_loop import run_change
from storyloop.base_design import approve_base_design
from storyloop.run_services import LoopServices
from storyloop.run_state import run_path
from storyloop.storage import load_json, write_json
from tests import test_base_agent_flow as flow


class BaseAgentRecoveryTests(unittest.TestCase):
    setUp = flow.BaseAgentFlowTests.setUp
    _services = flow.BaseAgentFlowTests._services

    def _approved(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, services=self._services())
        state = load_json(run_path(self.root, change_id))
        approve_base_design(self.root, change_id, state["base_design"]["plan_id"])
        run_change(self.root, change_id, services=self._services())

    def _writer(self, callback):
        services = self._services()
        return LoopServices(services.generate_smoke, callback, services.review,
                            design=services.design)

    def test_anchor_before_agent_call_restarts(self):
        self._approved()

        def interrupted(_project, _payload):
            raise RuntimeError("agent call interrupted")

        with self.assertRaisesRegex(RuntimeError, "agent call interrupted"):
            run_change(self.root, self.change["change_id"], services=self._writer(interrupted))
        saved = load_json(run_path(self.root, self.change["change_id"]))
        self.assertEqual(saved["agent_call_head"], saved["contract_commit"])
        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._services())["status"], "CONTRACT_COMMITTED")

    def test_agent_write_before_commit_restarts(self):
        self._approved()

        def interrupted(project, _payload):
            (project / "pom.xml").write_text("updated\n", encoding="utf-8")
            raise RuntimeError("agent interrupted after write")

        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._writer(interrupted))["status"],
                         "WORKSPACE_TAINTED")
        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._services())["status"], "CONTRACT_COMMITTED")
        self.assertFalse((self.root / "pom.xml").read_text(encoding="utf-8") == "updated\n")

    def _successful_writer(self):
        def write(project, _payload):
            (project / "pom.xml").write_text("<project><version>2</version></project>\n",
                                               encoding="utf-8")
            return {"summary": "updated"}
        return self._writer(write)

    def test_agent_staged_commit_failure_restarts(self):
        from storyloop import base_agent_execution
        self._approved()

        def fail_commit(project, paths, *_args, **_kwargs):
            _git(project, "add", "--", *paths)
            raise StoryLoopError("injected commit failure", code="GIT_BLOCKED")

        with mock.patch.object(base_agent_execution, "commit_paths", side_effect=fail_commit):
            with self.assertRaisesRegex(StoryLoopError, "injected commit failure"):
                run_change(self.root, self.change["change_id"], services=self._successful_writer())
        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._services())["status"], "CONTRACT_COMMITTED")
        self.assertFalse(_git(self.root, "diff", "--cached", "--name-only").strip())

    def test_agent_commit_before_state_restarts(self):
        from storyloop import base_agent_execution
        self._approved()
        original = base_agent_execution.save_run

        def interrupt(project, state):
            if state.get("candidate_commit"):
                raise RuntimeError("crash before candidate state")
            return original(project, state)

        with mock.patch.object(base_agent_execution, "save_run", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "crash before candidate state"):
                run_change(self.root, self.change["change_id"], services=self._successful_writer())
        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._services())["status"], "CONTRACT_COMMITTED")

    def test_candidate_evidence_write_before_state_restarts(self):
        from storyloop import base_execution
        self._approved()
        with mock.patch.object(base_execution, "_pause_review",
                               side_effect=RuntimeError("crash after evidence")):
            with self.assertRaisesRegex(RuntimeError, "crash after evidence"):
                run_change(self.root, self.change["change_id"], services=self._successful_writer())
        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._services())["status"], "CONTRACT_COMMITTED")
        evidence = self.root / ".storyloop/evidence" / self.change["change_id"] / "base-candidate-green.json"
        self.assertFalse(evidence.exists())

    def test_extra_commit_stops_tainted_without_deleting_it(self):
        from storyloop import base_agent_execution
        self._approved()
        original = base_agent_execution.save_run

        def interrupt(project, state):
            if state.get("candidate_commit"):
                raise RuntimeError("crash before candidate state")
            return original(project, state)

        with mock.patch.object(base_agent_execution, "save_run", side_effect=interrupt):
            with self.assertRaises(RuntimeError):
                run_change(self.root, self.change["change_id"], services=self._successful_writer())
        _git(self.root, "-c", "user.name=Human", "-c", "user.email=human@example.test",
             "commit", "--allow-empty", "-m", "manual extra")
        head = _git(self.root, "rev-parse", "HEAD").strip()
        result = run_change(self.root, self.change["change_id"], services=self._services())
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")
        self.assertEqual(_git(self.root, "rev-parse", "HEAD").strip(), head)

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

