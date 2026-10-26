"""Premerge agent design files are never accepted from an interrupted write."""

import os
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
    _agent_candidate = flow.BaseAgentFlowTests._agent_candidate

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

    def test_agent_ignored_file_is_removed_without_touching_preexisting_ignored_file(self):
        self._approved()
        exclude = self.root / ".git/info/exclude"
        exclude.write_text(exclude.read_text(encoding="utf-8") + "\nagent-cache/\n",
                           encoding="utf-8")
        cache = self.root / "agent-cache"
        cache.mkdir()
        retained = cache / "human.txt"
        retained.write_text("human\n", encoding="utf-8")
        self.assertEqual(_git(self.root, "status", "--porcelain=v1", "--untracked-files=all"), "")

        def interrupted(project, _payload):
            (project / "agent-cache/generated.txt").write_text("agent\n", encoding="utf-8")
            raise RuntimeError("agent interrupted")

        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._writer(interrupted))["status"],
                         "WORKSPACE_TAINTED")
        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._services())["status"], "CONTRACT_COMMITTED")
        self.assertTrue(retained.exists())
        self.assertFalse((cache / "generated.txt").exists())

    def test_agent_symlink_artifact_is_unlinked_without_following(self):
        self._approved()
        target = self.root.parent / "outside-agent-target.txt"
        target.write_text("outside\n", encoding="utf-8")
        self.addCleanup(target.unlink)

        def interrupted(project, _payload):
            (project / "agent-link").symlink_to(target)
            raise RuntimeError("agent interrupted")

        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._writer(interrupted))["status"],
                         "WORKSPACE_TAINTED")
        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._services())["status"], "CONTRACT_COMMITTED")
        self.assertFalse((self.root / "agent-link").is_symlink())
        self.assertEqual(target.read_text(encoding="utf-8"), "outside\n")

    def test_agent_hardlink_artifact_is_unlinked_without_deleting_target(self):
        self._approved()
        target = self.root.parent / "outside-agent-hardlink.txt"
        target.write_text("outside\n", encoding="utf-8")
        self.addCleanup(target.unlink)

        def interrupted(project, _payload):
            os.link(target, project / "agent-hardlink")
            raise RuntimeError("agent interrupted")

        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._writer(interrupted))["status"],
                         "WORKSPACE_TAINTED")
        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._services())["status"], "CONTRACT_COMMITTED")
        self.assertFalse((self.root / "agent-hardlink").exists())
        self.assertEqual(target.read_text(encoding="utf-8"), "outside\n")

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

    def test_changed_agent_call_head_does_not_authorize_cleanup(self):
        self._approved()
        path = run_path(self.root, self.change["change_id"])
        state = load_json(path)
        from storyloop.base_agent_inventory import inventory
        files = inventory(self.root)
        state["agent_call_head"] = "f" * 40
        state["base_inflight"] = "agent_implementation"
        state["agent_before_inventory"] = files
        state["agent_after_inventory"] = files
        write_json(path, state)
        result = run_change(self.root, self.change["change_id"], services=self._services())
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")

    def _review_context(self):
        return {"implement_vendor": "openai", "review_vendor": "anthropic",
                "review_adapter": "reviewer", "available_vendors": {"openai", "anthropic"}}

    def _review_services(self):
        services = self._services()
        return LoopServices(services.generate_smoke, services.implement,
                            lambda *_: {"verdict": "pass", "summary": "独立审查通过",
                                        "findings": [], "questions": []},
                            design=services.design)

    def test_review_write_before_state_restarts(self):
        from storyloop import base_review
        self._agent_candidate()
        with mock.patch.object(base_review, "vendor_context", return_value=self._review_context()):
            with mock.patch.object(base_review, "save_run",
                                   side_effect=RuntimeError("crash after review write")):
                with self.assertRaisesRegex(RuntimeError, "crash after review write"):
                    run_change(self.root, self.change["change_id"],
                               services=self._review_services())
        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._services())["status"], "CONTRACT_COMMITTED")

    def test_review_tamper_restarts(self):
        from storyloop import base_review
        self._agent_candidate()
        with mock.patch.object(base_review, "vendor_context", return_value=self._review_context()):
            self.assertEqual(run_change(self.root, self.change["change_id"],
                                        services=self._review_services())["status"],
                             "MANUAL_ACCEPTANCE_REQUIRED")
        path = self.root / ".storyloop/state/base-reviews" / f"{self.change['change_id']}.json"
        record = load_json(path)
        record["verdict"] = "fail"
        write_json(path, record)
        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._services())["status"], "CONTRACT_COMMITTED")

    def test_failed_independent_review_discards_agent_candidate(self):
        from storyloop import base_review
        self._agent_candidate()
        services = self._services()
        reviewer = LoopServices(services.generate_smoke, services.implement,
                                lambda *_: {"verdict": "fail", "summary": "发现问题",
                                            "findings": [], "questions": []},
                                design=services.design)
        with mock.patch.object(base_review, "vendor_context", return_value=self._review_context()):
            result = run_change(self.root, self.change["change_id"], services=reviewer)
        self.assertEqual(result["status"], "CONTRACT_COMMITTED")
        self.assertEqual(_git(self.root, "rev-parse", "HEAD").strip(),
                         load_json(run_path(self.root, self.change["change_id"]))["contract_commit"])

    def test_acceptance_write_before_state_restarts(self):
        from storyloop import base_review, manual_acceptance
        self._agent_candidate()
        with mock.patch.object(base_review, "vendor_context", return_value=self._review_context()):
            run_change(self.root, self.change["change_id"], services=self._review_services())
        with mock.patch.object(manual_acceptance, "save_run",
                               side_effect=RuntimeError("crash after acceptance write")):
            with self.assertRaisesRegex(RuntimeError, "crash after acceptance write"):
                manual_acceptance.record_manual_acceptance(
                    self.root, self.change["change_id"], ["BASE-ACCEPT"], "人工已核对")
        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._services())["status"], "CONTRACT_COMMITTED")

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

    def test_modified_design_file_is_preserved_as_unknown_human_work(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, services=self._services())
        plan = self.root / ".storyloop/state/base-designs" / f"{change_id}.json"
        plan.write_text("human edit\n", encoding="utf-8")
        result = run_change(self.root, change_id, services=self._services())
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")
        self.assertEqual(plan.read_text(encoding="utf-8"), "human edit\n")

    def test_note_present_before_agent_anchor_is_not_deleted(self):
        self._approved()
        note = self.root / "notes.txt"
        note.write_text("human note\n", encoding="utf-8")
        with self.assertRaisesRegex(StoryLoopError, "clean contract commit"):
            run_change(self.root, self.change["change_id"], services=self._services())
        result = run_change(self.root, self.change["change_id"], services=self._services())
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")
        self.assertEqual(note.read_text(encoding="utf-8"), "human note\n")

    def test_note_added_after_agent_candidate_is_not_deleted(self):
        self._agent_candidate()
        note = self.root / "review-notes.txt"
        note.write_text("human review notes\n", encoding="utf-8")
        result = run_change(self.root, self.change["change_id"], services=self._services())
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")
        self.assertEqual(note.read_text(encoding="utf-8"), "human review notes\n")

