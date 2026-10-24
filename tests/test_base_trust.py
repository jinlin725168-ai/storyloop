"""The human base channel must not trust mutable artifacts without a state binding."""

import hashlib
import unittest
from unittest import mock

from storyloop import base_execution, base_upgrade
from storyloop.baseline import _git
from storyloop.manual_acceptance import record_manual_acceptance
from storyloop.models import StoryLoopError
from storyloop.project_loop import run_change
from storyloop.project_receipt import verify_verified_artifacts
from storyloop.run_state import run_path
from storyloop.run_guards import guard_g1
from storyloop.storage import load_json, write_json
from tests import test_base_lifecycle as lifecycle
from tests.test_baseline_behavior import git


class BaseTrustTests(unittest.TestCase):
    setUp = lifecycle.BaseG1Tests.setUp
    _accepted_candidate = lifecycle.BaseG1Tests._accepted_candidate

    def test_unbound_old_green_evidence_cannot_open_g1(self):
        base_upgrade.establish_old_green(self.root, self.change, self.state)
        with self.assertRaisesRegex(StoryLoopError, "old.*evidence|digest"):
            guard_g1(self.root, self.state, {})

    def test_tampered_manual_acceptance_requires_new_record(self):
        change_id = self._accepted_candidate()
        path = self.root / ".storyloop/state/acceptance" / f"{change_id}.json"
        value = load_json(path)
        value["records"][0]["note"] = "伪造的验收说明"
        write_json(path, value)
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "MANUAL_ACCEPTANCE_REQUIRED")
        record_manual_acceptance(self.root, change_id, ["BASE-ACCEPT"], "再次核对")
        self.assertEqual(run_change(self.root, change_id)["status"], "FINALIZING")

    def test_tampered_review_after_acceptance_returns_to_review_stop(self):
        change_id = self._accepted_candidate()
        path = base_execution._review_path(self.root, change_id)
        value = load_json(path)
        value["note"] = "伪造的审查说明"
        write_json(path, value)
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")
        base_execution.record_base_review(self.root, change_id, "pass", "重新审查")
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "MANUAL_ACCEPTANCE_REQUIRED")

    def test_review_can_be_rerecorded_after_report_commit_crash(self):
        change_id = self._accepted_candidate()
        original = base_execution.save_run

        def interrupt(project, state):
            if state["status"] == "VERIFIED":
                raise RuntimeError("report commit crash")
            return original(project, state)

        with mock.patch.object(base_execution, "save_run", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "report commit crash"):
                run_change(self.root, change_id)
        path = base_execution._review_path(self.root, change_id)
        value = load_json(path)
        value["note"] = "篡改"
        write_json(path, value)
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")
        base_execution.record_base_review(self.root, change_id, "pass", "重新审查")
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "MANUAL_ACCEPTANCE_REQUIRED")
        self.assertEqual(run_change(self.root, change_id)["status"], "FINALIZING")

    def test_tampered_evidence_after_report_commit_is_reverified(self):
        change_id = self._accepted_candidate()
        original = base_execution.save_run

        def interrupt(project, state):
            if state["status"] == "VERIFIED":
                raise RuntimeError("report commit crash")
            return original(project, state)

        with mock.patch.object(base_execution, "save_run", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "report commit crash"):
                run_change(self.root, change_id)
        evidence_path = base_execution._evidence_path(self.root, change_id)
        value = load_json(evidence_path)
        value["build"]["status"] = "FAILED"
        write_json(evidence_path, value)
        self.assertEqual(run_change(self.root, change_id)["status"], "FINALIZING")

    def test_tampered_acceptance_after_report_commit_can_be_rerecorded(self):
        change_id = self._accepted_candidate()
        original = base_execution.save_run

        def interrupt(project, state):
            if state["status"] == "VERIFIED":
                raise RuntimeError("report commit crash")
            return original(project, state)

        with mock.patch.object(base_execution, "save_run", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "report commit crash"):
                run_change(self.root, change_id)
        path = self.root / ".storyloop/state/acceptance" / f"{change_id}.json"
        value = load_json(path)
        value["records"][0]["note"] = "篡改"
        write_json(path, value)
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "MANUAL_ACCEPTANCE_REQUIRED")
        self.assertEqual(record_manual_acceptance(self.root, change_id, ["BASE-ACCEPT"],
                                                  "重新核对")["status"], "ACCEPTED")
        self.assertEqual(run_change(self.root, change_id)["status"], "FINALIZING")

    def test_report_commit_with_wrong_blob_is_corrected_before_merge(self):
        change_id = self._accepted_candidate()
        original = base_execution.save_run

        def interrupt(project, state):
            if state["status"] == "VERIFIED":
                raise RuntimeError("report commit crash")
            return original(project, state)

        with mock.patch.object(base_execution, "save_run", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "report commit crash"):
                run_change(self.root, change_id)
        report = self.root / "reports/storyloop" / change_id / "report.json"
        expected = report.read_bytes()
        relative = report.relative_to(self.root).as_posix()
        report.write_bytes(expected.replace(b'"PASSED"', b'"FORGED"', 1))
        git(self.root, "add", relative)
        git(self.root, "commit", "--amend", "-q", "--no-edit")
        git(self.root, "update-index", "--skip-worktree", relative)
        report.write_bytes(expected)
        with mock.patch.object(base_execution, "save_run", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "report commit crash"):
                run_change(self.root, change_id)
        self.assertEqual(run_change(self.root, change_id)["status"], "FINALIZING")
        state = load_json(run_path(self.root, change_id))
        self.assertEqual(_git(self.root, "show", f"{state['report_commit']}:{relative}").encode(),
                         expected)

    def _candidate(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, implementer="human")
        (self.root / "pom.xml").write_text("<project><version>2</version></project>\n",
                                             encoding="utf-8")
        git(self.root, "add", "pom.xml")
        git(self.root, "commit", "-q", "-m", "candidate")
        return change_id

    def test_unbound_candidate_evidence_is_reverified(self):
        change_id = self._candidate()
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")
        path = run_path(self.root, change_id)
        state = load_json(path)
        state["status"] = "IMPLEMENTING"
        state.pop("base_evidence_sha256", None)
        write_json(path, state)
        with mock.patch.object(base_execution, "run_commands_isolated",
                               side_effect=AssertionError("unbound checks reran")):
            with self.assertRaisesRegex(AssertionError, "unbound checks reran"):
                run_change(self.root, change_id)

    def test_tampered_candidate_evidence_is_reverified(self):
        change_id = self._candidate()
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")
        evidence_path = base_execution._evidence_path(self.root, change_id)
        evidence = load_json(evidence_path)
        evidence["layers"]["unit"]["status"] = "FAILED"
        write_json(evidence_path, evidence)
        path = run_path(self.root, change_id)
        state = load_json(path)
        state["status"] = "IMPLEMENTING"
        write_json(path, state)
        with mock.patch.object(base_execution, "run_commands_isolated",
                               side_effect=AssertionError("tampered checks reran")):
            with self.assertRaisesRegex(AssertionError, "tampered checks reran"):
                run_change(self.root, change_id)

    def test_unverified_implementing_candidate_cannot_be_reviewed(self):
        change_id = self._candidate()
        path = run_path(self.root, change_id)
        state = load_json(path)
        state["status"] = "IMPLEMENTING"
        state["candidate_commit"] = git(self.root, "rev-parse", "HEAD")
        write_json(path, state)
        git(self.root, "config", "user.name", "Independent Reviewer")
        with self.assertRaisesRegex(StoryLoopError, "verified|green|review"):
            base_execution.record_base_review(self.root, change_id, "pass", "未验证")

    def test_rejected_review_does_not_block_new_candidate_review(self):
        change_id = self._candidate()
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")
        git(self.root, "config", "user.name", "Independent Reviewer")
        base_execution.record_base_review(self.root, change_id, "fail", "需修订")
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")
        git(self.root, "config", "user.name", "StoryLoop Test")
        (self.root / "pom.xml").write_text("<project><version>3</version></project>\n",
                                             encoding="utf-8")
        git(self.root, "add", "pom.xml")
        git(self.root, "commit", "-q", "-m", "revised candidate")
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")
        git(self.root, "config", "user.name", "Independent Reviewer")
        base_execution.record_base_review(self.root, change_id, "pass", "重审通过")
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "MANUAL_ACCEPTANCE_REQUIRED")

    def test_tampered_review_record_requires_new_review(self):
        change_id = self._candidate()
        run_change(self.root, change_id)
        git(self.root, "config", "user.name", "Independent Reviewer")
        base_execution.record_base_review(self.root, change_id, "pass", "已核对")
        path = base_execution._review_path(self.root, change_id)
        review = load_json(path)
        review["note"] = "被篡改"
        write_json(path, review)
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")

    def test_tampered_evidence_at_review_rechecks_candidate(self):
        change_id = self._candidate()
        run_change(self.root, change_id)
        path = base_execution._evidence_path(self.root, change_id)
        value = load_json(path)
        value["build"]["status"] = "FAILED"
        write_json(path, value)
        with mock.patch.object(base_execution, "run_commands_isolated",
                               side_effect=AssertionError("review checks reran")):
            with self.assertRaisesRegex(AssertionError, "review checks reran"):
                run_change(self.root, change_id)

    def test_tampered_evidence_after_acceptance_rechecks_candidate(self):
        change_id = self._accepted_candidate()
        path = base_execution._evidence_path(self.root, change_id)
        value = load_json(path)
        value["build"]["status"] = "FAILED"
        write_json(path, value)
        with mock.patch.object(base_execution, "run_commands_isolated",
                               side_effect=AssertionError("acceptance checks reran")):
            with self.assertRaisesRegex(AssertionError, "acceptance checks reran"):
                run_change(self.root, change_id)

    def test_skip_worktree_report_cannot_replace_committed_report_digest(self):
        change_id = self.change["change_id"]
        report = self.root / "reports/storyloop" / change_id / "report.json"
        report.parent.mkdir(parents=True)
        report.write_bytes(b'{"status":"PASSED"}\n')
        relative = report.relative_to(self.root).as_posix()
        git(self.root, "add", relative)
        git(self.root, "commit", "-q", "-m", "report")
        commit = git(self.root, "rev-parse", "HEAD")
        evidence = base_execution._evidence_path(self.root, change_id)
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_bytes(b'{}\n')
        git(self.root, "update-index", "--skip-worktree", relative)
        report.write_bytes(b'{"status":"FORGED"}\n')
        state = {"change_id": change_id, "report": str(report), "report_commit": commit,
                 "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
                 "acceptance_evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}
        with self.assertRaisesRegex(StoryLoopError, "Verified acceptance report"):
            verify_verified_artifacts(self.root, state)

    def test_clean_autocrlf_checkout_keeps_accepted_base_candidate(self):
        git(self.root, "config", "core.autocrlf", "true")
        change_id = self._accepted_candidate()
        candidate = self.root / "pom.xml"
        candidate.write_bytes(candidate.read_bytes().replace(b"\r\n", b"\n")
                              .replace(b"\n", b"\r\n"))
        git(self.root, "add", "pom.xml")
        self.assertIn(b"\r\n", candidate.read_bytes())
        self.assertEqual(git(self.root, "status", "--porcelain"), "")
        self.assertEqual(run_change(self.root, change_id)["status"], "FINALIZING")

    def test_clean_autocrlf_checkout_allows_base_manual_acceptance(self):
        git(self.root, "config", "core.autocrlf", "true")
        change_id = self._candidate()
        run_change(self.root, change_id)
        git(self.root, "config", "user.name", "Independent Reviewer")
        base_execution.record_base_review(self.root, change_id, "pass", "已核对")
        run_change(self.root, change_id)
        candidate = self.root / "pom.xml"
        candidate.write_bytes(candidate.read_bytes().replace(b"\r\n", b"\n")
                              .replace(b"\n", b"\r\n"))
        git(self.root, "add", "pom.xml")
        self.assertIn(b"\r\n", candidate.read_bytes())
        self.assertEqual(git(self.root, "status", "--porcelain"), "")
        self.assertEqual(record_manual_acceptance(self.root, change_id, ["BASE-ACCEPT"],
                                                  "满足目标")["status"], "ACCEPTED")
