"""Base authorization, report integrity and clean Git checkouts remain guarded."""

import hashlib
import unittest

from storyloop import base_execution, base_upgrade
from storyloop.manual_acceptance import record_manual_acceptance
from storyloop.models import StoryLoopError
from storyloop.project_loop import run_change
from storyloop.project_receipt import verify_verified_artifacts
from storyloop.run_guards import guard_g1
from storyloop.run_state import run_path
from storyloop.storage import load_json, write_json
from tests import test_base_lifecycle as lifecycle
from tests.test_baseline_behavior import git


class BaseTrustTests(unittest.TestCase):
    setUp = lifecycle.BaseG1Tests.setUp
    _accepted_candidate = lifecycle.BaseG1Tests._accepted_candidate

    def _candidate(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, implementer="human")
        (self.root / "pom.xml").write_text("<project><version>2</version></project>\n",
                                             encoding="utf-8")
        git(self.root, "add", "pom.xml")
        git(self.root, "commit", "-q", "-m", "candidate")
        return change_id

    def test_unbound_old_green_evidence_cannot_open_g1(self):
        base_upgrade.establish_old_green(self.root, self.change, self.state)
        with self.assertRaisesRegex(StoryLoopError, "old.*evidence|digest"):
            guard_g1(self.root, self.state, {})

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

    def test_rejected_review_needs_a_new_human_commit(self):
        change_id = self._candidate()
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")
        git(self.root, "config", "user.name", "Independent Reviewer")
        base_execution.record_base_review(self.root, change_id, "fail", "需修订")
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")
        git(self.root, "config", "user.name", "StoryLoop Test")
        (self.root / "pom.xml").write_text("<project><version>3</version></project>\n",
                                             encoding="utf-8")
        git(self.root, "add", "pom.xml")
        git(self.root, "commit", "-q", "-m", "revised candidate")
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")

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
