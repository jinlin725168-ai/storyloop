"""Before merge, a base interruption discards engine output and restarts one attempt."""

from pathlib import Path
import unittest
from unittest import mock

from storyloop import base_execution
from storyloop.manual_acceptance import record_manual_acceptance
from storyloop.models import StoryLoopError
from storyloop.project_loop import run_change
from storyloop.run_recovery import reset_run
from storyloop.run_state import run_path
from storyloop.storage import load_json, locked, write_json
from tests import test_base_lifecycle as lifecycle
from tests.test_baseline_behavior import git


class BaseRestartTests(unittest.TestCase):
    setUp = lifecycle.BaseG1Tests.setUp
    _accepted_candidate = lifecycle.BaseG1Tests._accepted_candidate

    def _paths(self, change_id: str) -> dict[str, Path]:
        return {
            "old_green": self.root / ".storyloop/evidence" / change_id / "base-old-green.json",
            "candidate": self.root / ".storyloop/evidence" / change_id / "base-candidate-green.json",
            "review": self.root / ".storyloop/state/base-reviews" / f"{change_id}.json",
            "acceptance": self.root / ".storyloop/state/acceptance" / f"{change_id}.json",
            "report": self.root / "reports/storyloop" / change_id / "report.json",
            "markdown": self.root / "reports/storyloop" / change_id / "report.md",
        }

    def _assert_discarded(self, change_id: str, candidate: str) -> None:
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), candidate)
        self.assertEqual(load_json(run_path(self.root, change_id))["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")
        for path in self._paths(change_id).values():
            self.assertFalse(path.exists(), str(path))

    def test_tampered_evidence_discards_entire_attempt_then_revalidates(self):
        change_id = self._accepted_candidate()
        candidate = git(self.root, "rev-parse", "HEAD")
        evidence = self._paths(change_id)["candidate"]
        value = load_json(evidence)
        value["status"] = "FORGED"
        write_json(evidence, value)
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")
        self._assert_discarded(change_id, candidate)
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")
        base_execution.record_base_review(self.root, change_id, "pass", "重新审查")
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "MANUAL_ACCEPTANCE_REQUIRED")
        record_manual_acceptance(self.root, change_id, ["BASE-ACCEPT"], "重新验收")
        self.assertEqual(run_change(self.root, change_id)["status"], "FINALIZING")

    def test_tampered_review_discards_review_acceptance_and_evidence(self):
        change_id = self._accepted_candidate()
        candidate = git(self.root, "rev-parse", "HEAD")
        review = self._paths(change_id)["review"]
        value = load_json(review)
        value["reviewer"] = "candidate author"
        write_json(review, value)
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")
        self._assert_discarded(change_id, candidate)

    def test_review_snapshot_without_digest_or_file_cannot_be_used(self):
        change_id = self._accepted_candidate()
        candidate = git(self.root, "rev-parse", "HEAD")
        self._paths(change_id)["review"].unlink()
        state_path = run_path(self.root, change_id)
        state = load_json(state_path)
        state.pop("base_review_sha256")
        write_json(state_path, state)
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")
        self._assert_discarded(change_id, candidate)

    def test_tampered_acceptance_discards_review_and_requires_new_acceptance(self):
        change_id = self._accepted_candidate()
        candidate = git(self.root, "rev-parse", "HEAD")
        acceptance = self._paths(change_id)["acceptance"]
        value = load_json(acceptance)
        value["records"][0]["note"] = "changed"
        write_json(acceptance, value)
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")
        self._assert_discarded(change_id, candidate)

    def test_report_files_written_before_commit_are_discarded(self):
        change_id = self._accepted_candidate()
        candidate = git(self.root, "rev-parse", "HEAD")
        original = base_execution.commit_paths

        def interrupt(project, paths, message, **kwargs):
            if message.startswith("docs("):
                raise RuntimeError("crash before report commit")
            return original(project, paths, message, **kwargs)

        with mock.patch.object(base_execution, "commit_paths", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "crash before report commit"):
                run_change(self.root, change_id)
        self.assertTrue(self._paths(change_id)["report"].exists())
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")
        self._assert_discarded(change_id, candidate)

    def test_unfinished_implementing_checkpoint_does_not_reuse_saved_evidence(self):
        change_id = self._accepted_candidate()
        candidate = git(self.root, "rev-parse", "HEAD")
        state_path = run_path(self.root, change_id)
        state = load_json(state_path)
        state["status"] = "IMPLEMENTING"
        write_json(state_path, state)
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")
        self._assert_discarded(change_id, candidate)

    def test_report_commit_crash_discards_engine_commit_and_human_records(self):
        change_id = self._accepted_candidate()
        candidate = git(self.root, "rev-parse", "HEAD")
        original = base_execution.save_run

        def interrupt(project, state):
            if state["status"] == "VERIFIED":
                raise RuntimeError("crash after report commit")
            return original(project, state)

        with mock.patch.object(base_execution, "save_run", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "crash after report commit"):
                run_change(self.root, change_id)
        self.assertNotEqual(git(self.root, "rev-parse", "HEAD"), candidate)
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")
        self._assert_discarded(change_id, candidate)

    def test_old_green_crash_restarts_at_committed_contract(self):
        change_id = self.change["change_id"]
        original = base_execution.establish_old_green

        def interrupt(project, change, state):
            original(project, change, state)
            raise RuntimeError("crash after old green")

        with mock.patch.object(base_execution, "establish_old_green", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "crash after old green"):
                run_change(self.root, change_id, implementer="human")
        self.assertEqual(run_change(self.root, change_id)["status"], "CONTRACT_COMMITTED")
        self.assertFalse(self._paths(change_id)["old_green"].exists())
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")

    def test_reset_premerge_uses_same_human_restart_point(self):
        change_id = self._accepted_candidate()
        candidate = git(self.root, "rev-parse", "HEAD")
        result = reset_run(self.root, change_id, "重新验证")
        self.assertEqual(result["status"], "HUMAN_IMPLEMENTATION_REQUIRED")
        self._assert_discarded(change_id, candidate)

    def test_reset_from_premerge_tainted_state_keeps_the_human_candidate(self):
        change_id = self._accepted_candidate()
        candidate = git(self.root, "rev-parse", "HEAD")
        path = run_path(self.root, change_id)
        state = load_json(path)
        state["interrupted_status"] = "MANUAL_ACCEPTANCE_REQUIRED"
        state["status"] = "WORKSPACE_TAINTED"
        write_json(path, state)
        self.assertEqual(reset_run(self.root, change_id, "工作区异常")["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")
        self._assert_discarded(change_id, candidate)

    def test_reset_after_merge_cannot_return_to_preparation(self):
        change_id = self._accepted_candidate()
        self.assertEqual(run_change(self.root, change_id)["status"], "FINALIZING")
        before = load_json(run_path(self.root, change_id))
        with self.assertRaisesRegex(StoryLoopError, "merged base|finaliz"):
            reset_run(self.root, change_id, "重新验证")
        self.assertEqual(load_json(run_path(self.root, change_id)), before)

    def test_reset_from_finalization_baseline_stop_cannot_reopen_base_branch(self):
        change_id = self._accepted_candidate()
        self.assertEqual(run_change(self.root, change_id)["status"], "FINALIZING")
        baseline_path = self.root / ".storyloop/baseline.json"
        original = baseline_path.read_bytes()
        baseline = load_json(baseline_path)
        write_json(baseline_path, {**baseline, "baseline_version": "0.9.9"})
        self.assertEqual(run_change(self.root, change_id)["status"], "WORKSPACE_TAINTED")
        state_path = run_path(self.root, change_id)
        before = load_json(state_path)
        git(self.root, "checkout", before["feature_branch"])
        with self.assertRaisesRegex(StoryLoopError, "Merged base runs cannot reset"):
            reset_run(self.root, change_id, "不应回退")
        self.assertEqual(load_json(state_path), before)
        git(self.root, "checkout", before["source_branch"])
        baseline_path.write_bytes(original)
        self.assertEqual(run_change(self.root, change_id)["status"], "PASSED")

    def test_amended_human_candidate_restarts_without_old_commit_ancestry(self):
        change_id = self._accepted_candidate()
        git(self.root, "commit", "--amend", "-q", "-m", "amended human candidate")
        amended = git(self.root, "rev-parse", "HEAD")
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")
        self._assert_discarded(change_id, amended)
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")

    def test_merge_before_state_save_cannot_be_reset_and_run_finishes_receipt(self):
        change_id = self._accepted_candidate()
        original = base_execution.save_run

        def interrupt(project, state):
            if state["status"] == "MERGED":
                raise RuntimeError("crash after source merge")
            return original(project, state)

        with mock.patch.object(base_execution, "save_run", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "crash after source merge"):
                run_change(self.root, change_id)
        state = load_json(run_path(self.root, change_id))
        self.assertEqual(state["status"], "VERIFIED")
        merged = git(self.root, "rev-parse", "HEAD")
        git(self.root, "switch", state["feature_branch"])
        with self.assertRaisesRegex(StoryLoopError, "Merged base runs cannot reset"):
            reset_run(self.root, change_id, "不应回退已合并运行")
        self.assertEqual(run_change(self.root, change_id)["status"], "FINALIZING")
        self.assertEqual(git(self.root, "rev-parse", "HEAD^"), merged)
        self.assertEqual(run_change(self.root, change_id)["status"], "PASSED")

    def test_staged_report_after_failed_commit_is_unstaged_on_restart(self):
        change_id = self._accepted_candidate()
        candidate = git(self.root, "rev-parse", "HEAD")
        original_commit = base_execution.commit_paths

        def blocked(project, paths, message, **kwargs):
            if message.startswith("docs("):
                git(project, "add", "--", *paths)
                raise StoryLoopError("signing failed", code="GIT_BLOCKED")
            return original_commit(project, paths, message, **kwargs)

        with mock.patch.object(base_execution, "commit_paths", side_effect=blocked):
            with self.assertRaisesRegex(StoryLoopError, "signing failed"):
                run_change(self.root, change_id)
        self.assertIn("reports/storyloop", git(self.root, "status", "--porcelain"))
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")
        self._assert_discarded(change_id, candidate)
        self.assertEqual(git(self.root, "status", "--porcelain"), "")
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")

    def test_reset_refuses_to_race_with_an_active_run(self):
        change_id = self._accepted_candidate()
        before = load_json(run_path(self.root, change_id))
        with locked(self.root):
            with self.assertRaisesRegex(StoryLoopError, "project lock"):
                reset_run(self.root, change_id, "并发复位")
        self.assertEqual(load_json(run_path(self.root, change_id)), before)

    def test_candidate_paths_are_checked_before_reproving_old_green(self):
        change_id = self.change["change_id"]
        self.assertEqual(run_change(self.root, change_id, implementer="human")["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")
        with mock.patch.object(base_execution, "_prove_old_green",
                               side_effect=AssertionError("old green reran")) as prove:
            self.assertEqual(run_change(self.root, change_id)["status"],
                             "CONTRACT_COMMITTED")
        prove.assert_not_called()

    def test_valid_candidate_reuses_current_g1_without_rerunning_old_green(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, implementer="human")
        (self.root / "pom.xml").write_text("<project><version>2</version></project>\n",
                                            encoding="utf-8")
        git(self.root, "add", "pom.xml")
        git(self.root, "commit", "-q", "-m", "human candidate")
        with mock.patch.object(base_execution, "_prove_old_green",
                               side_effect=AssertionError("old green reran")) as prove:
            self.assertEqual(run_change(self.root, change_id)["status"],
                             "BASE_REVIEW_REQUIRED")
        prove.assert_not_called()

