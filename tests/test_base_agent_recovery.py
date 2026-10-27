"""Premerge agent design files are never accepted from an interrupted write."""

from pathlib import Path
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

    def test_interrupted_detached_checkout_is_discarded_without_main_edits(self):
        self._approved()
        main_content = (self.root / "pom.xml").read_bytes()

        def interrupted(checkout, _payload):
            self.assertNotEqual(checkout, self.root)
            (checkout / "pom.xml").write_text("agent\n", encoding="utf-8")
            (checkout / "untracked.txt").write_text("agent\n", encoding="utf-8")
            raise RuntimeError("agent crashed")

        with self.assertRaisesRegex(RuntimeError, "agent crashed"):
            run_change(self.root, self.change["change_id"], services=self._writer(interrupted))
        state = load_json(run_path(self.root, self.change["change_id"]))
        checkout = Path(state["agent_worktree"]["path"])
        self.assertTrue(checkout.exists())
        self.assertEqual((self.root / "pom.xml").read_bytes(), main_content)
        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._services())["status"], "CONTRACT_COMMITTED")
        self.assertFalse(checkout.exists())

    def test_human_note_after_agent_crash_is_preserved_until_cleaned(self):
        self._approved()

        def interrupted(checkout, _payload):
            (checkout / "pom.xml").write_text("agent\n", encoding="utf-8")
            raise RuntimeError("agent crashed")

        with self.assertRaises(RuntimeError):
            run_change(self.root, self.change["change_id"], services=self._writer(interrupted))
        state = load_json(run_path(self.root, self.change["change_id"]))
        checkout = Path(state["agent_worktree"]["path"])
        note = self.root / "human-note.txt"
        note.write_text("human\n", encoding="utf-8")
        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._services())["status"], "WORKSPACE_TAINTED")
        self.assertFalse(checkout.exists())
        self.assertEqual(note.read_text(encoding="utf-8"), "human\n")
        note.unlink()
        from storyloop.run_recovery import reset_run
        self.assertEqual(reset_run(self.root, self.change["change_id"], "已检查笔记")["status"],
                         "CONTRACT_COMMITTED")

    def test_agent_changed_ignore_rules_cannot_delete_human_ignored_file(self):
        self._approved()
        exclude = self.root / ".git/info/exclude"
        exclude.write_text(exclude.read_text(encoding="utf-8") + "\nprivate-cache/\n",
                           encoding="utf-8")
        private = self.root / "private-cache/human.txt"
        private.parent.mkdir()
        private.write_text("human\n", encoding="utf-8")

        def write(checkout, _payload):
            (checkout / ".gitignore").write_text("private-cache/\n", encoding="utf-8")
            (checkout / "pom.xml").write_text("agent\n", encoding="utf-8")
            return {"summary": "changed"}

        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._writer(write))["status"], "WORKSPACE_TAINTED")
        self.assertEqual(private.read_text(encoding="utf-8"), "human\n")
        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._services())["status"], "CONTRACT_COMMITTED")

    def test_human_tracked_edit_after_crash_is_not_restored(self):
        self._approved()

        def interrupted(checkout, _payload):
            (checkout / "pom.xml").write_text("agent\n", encoding="utf-8")
            raise RuntimeError("agent crashed")

        with self.assertRaises(RuntimeError):
            run_change(self.root, self.change["change_id"], services=self._writer(interrupted))
        main = self.root / "pom.xml"
        main.write_text("human change\n", encoding="utf-8")
        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._services())["status"], "WORKSPACE_TAINTED")
        self.assertEqual(main.read_text(encoding="utf-8"), "human change\n")
        _git(self.root, "restore", "--", "pom.xml")
        from storyloop.run_recovery import reset_run
        self.assertEqual(reset_run(self.root, self.change["change_id"], "人工已移走修改")["status"],
                         "CONTRACT_COMMITTED")

    def test_agent_unignored_large_tree_does_not_block_cleanup(self):
        from storyloop import base_agent_transfer
        self._approved()
        original = base_agent_transfer.git

        def fail_large(checkout, *args):
            if args[:2] == ("ls-files", "--others"):
                raise StoryLoopError("Git output exceeded limit", code="GIT_BLOCKED")
            return original(checkout, *args)

        def write(checkout, _payload):
            folder = checkout / "node_modules"
            folder.mkdir()
            (folder / "many.txt").write_text("agent\n", encoding="utf-8")
            return {"summary": "changed"}

        with mock.patch.object(base_agent_transfer, "git", side_effect=fail_large):
            self.assertEqual(run_change(self.root, self.change["change_id"],
                                        services=self._writer(write))["status"], "WORKSPACE_TAINTED")
        self.assertFalse((self.root / "node_modules").exists())
        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._services())["status"], "CONTRACT_COMMITTED")

    def test_anchor_saved_before_worktree_add_failure_restarts(self):
        from storyloop import base_agent_worktree
        self._approved()
        original = base_agent_worktree.git

        def fail_add(project, *args):
            if args[:2] == ("worktree", "add"):
                raise StoryLoopError("injected add failure", code="GIT_BLOCKED")
            return original(project, *args)

        with mock.patch.object(base_agent_worktree, "git", side_effect=fail_add):
            with self.assertRaisesRegex(StoryLoopError, "injected add failure"):
                run_change(self.root, self.change["change_id"], services=self._services())
        state = load_json(run_path(self.root, self.change["change_id"]))
        self.assertEqual(state["agent_worktree"]["contract_commit"], state["contract_commit"])
        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._services())["status"], "CONTRACT_COMMITTED")

    def test_audit_before_transfer_interruption_discards_checkout(self):
        from storyloop import base_agent_execution
        self._approved()
        with mock.patch.object(base_agent_execution, "transfer_agent_candidate",
                               side_effect=RuntimeError("crash before transfer")):
            with self.assertRaisesRegex(RuntimeError, "crash before transfer"):
                run_change(self.root, self.change["change_id"],
                           services=self._successful_writer())
        state = load_json(run_path(self.root, self.change["change_id"]))
        checkout = Path(state["agent_worktree"]["path"])
        self.assertTrue(checkout.exists())
        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._services())["status"], "CONTRACT_COMMITTED")
        self.assertFalse(checkout.exists())

    def test_transfer_write_interruption_preserves_main_file(self):
        from storyloop import base_agent_transfer
        from storyloop.storage import write_bytes_atomic
        self._approved()

        def interrupt(path, content):
            write_bytes_atomic(path, content)
            raise RuntimeError("crash during transfer")

        with mock.patch.object(base_agent_transfer, "write_bytes_atomic", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "crash during transfer"):
                run_change(self.root, self.change["change_id"],
                           services=self._successful_writer())
        changed = (self.root / "pom.xml").read_bytes()
        self.assertIn(b"version>2", changed)
        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._services())["status"], "WORKSPACE_TAINTED")
        self.assertEqual((self.root / "pom.xml").read_bytes(), changed)
        _git(self.root, "restore", "--", "pom.xml")
        from storyloop.run_recovery import reset_run
        self.assertEqual(reset_run(self.root, self.change["change_id"], "已移走搬回内容")["status"],
                         "CONTRACT_COMMITTED")

    def test_candidate_commit_failure_preserves_index_until_human_cleans(self):
        from storyloop import base_agent_transfer
        self._approved()

        def fail_commit(project, paths, *_args, **_kwargs):
            _git(project, "add", "--", *paths)
            raise StoryLoopError("injected candidate commit failure", code="GIT_BLOCKED")

        with mock.patch.object(base_agent_transfer, "commit_paths", side_effect=fail_commit):
            result = run_change(self.root, self.change["change_id"],
                                services=self._successful_writer())
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")
        self.assertIn("pom.xml", _git(self.root, "diff", "--cached", "--name-only"))
        _git(self.root, "reset", "-q", "--", "pom.xml")
        _git(self.root, "restore", "--", "pom.xml")
        from storyloop.run_recovery import reset_run
        self.assertEqual(reset_run(self.root, self.change["change_id"], "已清理候选索引")["status"],
                         "CONTRACT_COMMITTED")

    def test_anchor_before_agent_call_restarts(self):
        self._approved()

        def interrupted(_project, _payload):
            raise RuntimeError("agent call interrupted")

        with self.assertRaisesRegex(RuntimeError, "agent call interrupted"):
            run_change(self.root, self.change["change_id"], services=self._writer(interrupted))
        saved = load_json(run_path(self.root, self.change["change_id"]))
        self.assertEqual(saved["agent_worktree"]["contract_commit"], saved["contract_commit"])
        self.assertEqual(run_change(self.root, self.change["change_id"],
                                    services=self._services())["status"], "CONTRACT_COMMITTED")

    def _successful_writer(self):
        def write(project, _payload):
            (project / "pom.xml").write_text("<project><version>2</version></project>\n",
                                               encoding="utf-8")
            return {"summary": "updated"}
        return self._writer(write)

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

    def test_old_agent_binding_git_config_hash_does_not_block_reset(self):
        from storyloop.run_recovery import reset_run
        self._approved()
        path = run_path(self.root, self.change["change_id"])
        state = load_json(path)
        from storyloop.base_agent_execution import agent_control_binding
        old_binding = agent_control_binding(self.root, self.change["change_id"])
        old_binding[".git/config"] = "old hash"
        state["agent_control_binding"] = old_binding
        state["agent_call_head"] = state["contract_commit"]
        state["base_inflight"] = "agent_implementation"
        write_json(path, state)
        self.assertEqual(reset_run(self.root, self.change["change_id"], "回退过度绑定")["status"],
                         "CONTRACT_COMMITTED")

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
