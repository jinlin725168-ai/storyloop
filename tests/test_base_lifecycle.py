"""The base G1 gate uses green checks on the locked old foundation."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from storyloop import base_upgrade
from storyloop import base_execution
from storyloop.approvals import approve_change
from storyloop.baseline import load_baseline, load_project_config
from storyloop.cli import dispatch, parser
from storyloop.models import StoryLoopError
from storyloop.manual_acceptance import record_manual_acceptance
from storyloop.onboarding_cli import bootstrap_with_project_checks as bootstrap_project
from storyloop.project_loop import run_change
from storyloop.run_guards import guard_g1
from storyloop.storage import load_json, write_json
from tests.test_baseline_behavior import git, repository


class BaseG1Tests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        repository(self.root)
        smoke = self.root / "acceptance/smoke"
        smoke.mkdir(parents=True)
        (smoke / "check.py").write_text("print('smoke green')\n", encoding="utf-8")
        (smoke / "manifest.json").write_text(
            '{"version":1,"tests":[{"id":"check","description":"check",'
            '"command":["{python}","acceptance/smoke/check.py"],"timeout_seconds":30}]}',
            encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "smoke")
        bootstrap_project(self.root, {
            "unit_commands": [[sys.executable, "-c", "print('unit green')"]],
            "integration_commands": [], "check_commands": [],
            "base_build_commands": [],
        })
        result = base_upgrade.propose_base_change(self.root, ["pom.xml"], "更新构建配置")
        self.change = approve_change(self.root, result["change_id"])
        self.state = {"change_id": result["change_id"],
                      "contract_digest": self.change["approval"]["contract_digest"],
                      "contract_commit": load_baseline(self.root)["source_head"]}

    def _accepted_candidate(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, implementer="human")
        (self.root / "pom.xml").write_text("<project><version>2</version></project>\n",
                                             encoding="utf-8")
        git(self.root, "add", "pom.xml")
        git(self.root, "commit", "-q", "-m", "candidate")
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")
        git(self.root, "config", "user.name", "Independent Reviewer")
        base_execution.record_base_review(self.root, change_id, "pass", "已核对")
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "MANUAL_ACCEPTANCE_REQUIRED")
        record_manual_acceptance(self.root, change_id, ["BASE-ACCEPT"], "满足目标")
        return change_id

    def test_old_green_evidence_opens_base_g1(self):
        evidence = base_upgrade.establish_old_green(self.root, self.change, self.state)
        self.assertEqual(evidence["status"], "PASSED")
        self.assertEqual(evidence["build"]["status"], "DECLARED_ABSENT")
        self.assertEqual(evidence["layers"]["unit"]["status"], "PASSED")
        guard_g1(self.root, self.state, {})

    def test_tampered_old_green_evidence_cannot_open_g1(self):
        base_upgrade.establish_old_green(self.root, self.change, self.state)
        path = self.root / ".storyloop/evidence" / self.state["change_id"] / "base-old-green.json"
        value = load_json(path)
        value["contract_digest"] = "0" * 64
        from storyloop.storage import write_json
        write_json(path, value)
        with self.assertRaises(StoryLoopError):
            guard_g1(self.root, self.state, {})

    def test_g1_rejects_changed_approved_command_digest(self):
        base_upgrade.establish_old_green(self.root, self.change, self.state)
        path = self.root / ".storyloop/evidence" / self.state["change_id"] / "base-old-green.json"
        evidence = load_json(path)
        evidence["commands_sha256"] = "0" * 64
        write_json(path, evidence)
        with self.assertRaisesRegex(StoryLoopError, "commands"):
            guard_g1(self.root, self.state, {})

    def test_human_run_opens_branch_and_stops_without_design_agent(self):
        result = run_change(self.root, self.change["change_id"], implementer="human")
        self.assertEqual(result["status"], "HUMAN_IMPLEMENTATION_REQUIRED")
        self.assertEqual(git(self.root, "branch", "--show-current"),
                         f"storyloop/{self.change['change_id']}")

    def test_base_run_records_engine_source_and_target_tree(self):
        run_change(self.root, self.change["change_id"], implementer="human")
        state = load_json(self.root / ".storyloop/state/runs" / f"{self.change['change_id']}.json")
        evidence = load_json(self.root / ".storyloop/evidence" / self.change["change_id"] /
                             "base-old-green.json")
        self.assertEqual(state["engine_version"], evidence["engine_version"])
        self.assertEqual(state["engine_source"], evidence["engine_source"])
        self.assertEqual(state["target_tree_oid"], evidence["target_tree_oid"])
        self.assertEqual(state["target_tree_oid"],
                         git(self.root, "rev-parse", f"{state['source_head']}^{{tree}}"))

    def test_human_candidate_rejects_unapproved_committed_path(self):
        run_change(self.root, self.change["change_id"], implementer="human")
        (self.root / "rogue.py").write_text("VALUE = 1\n", encoding="utf-8")
        git(self.root, "add", "rogue.py")
        git(self.root, "commit", "-q", "-m", "rogue")
        with self.assertRaisesRegex(StoryLoopError, "rogue.py"):
            run_change(self.root, self.change["change_id"])

    def test_human_candidate_rejects_git_symlink_mode(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, implementer="human")
        git(self.root, "config", "core.symlinks", "false")
        blob = git(self.root, "hash-object", "-w", "pom.xml")
        git(self.root, "update-index", "--cacheinfo", f"120000,{blob},pom.xml")
        git(self.root, "commit", "-q", "-m", "base symlink")
        git(self.root, "checkout", "-f", "HEAD", "--", "pom.xml")
        with self.assertRaisesRegex(StoryLoopError, "symlink"):
            run_change(self.root, change_id)

    def test_cli_approval_does_not_start_base_run(self):
        source = git(self.root, "branch", "--show-current")
        args = parser().parse_args(["approve", str(self.root), "--change",
                                    self.change["change_id"], "--no-interactive"])
        result = dispatch(args)
        self.assertEqual(result["status"], "APPROVED")
        self.assertEqual(git(self.root, "branch", "--show-current"), source)

    def test_skills_scope_cannot_commit_arbitrary_foundation_file(self):
        proposed = base_upgrade.propose_base_change(self.root, ["docs/base.md"], "刷新技能")
        path = self.root / ".storyloop/state/changes" / f"{proposed['change_id']}.json"
        draft = load_json(path)
        draft["product"]["base"]["scope"] = "skills"
        write_json(path, draft)
        approved = approve_change(self.root, proposed["change_id"])
        with self.assertRaisesRegex(StoryLoopError, "scope"):
            run_change(self.root, approved["change_id"], implementer="human")
        self.assertEqual(git(self.root, "branch", "--show-current"),
                         load_baseline(self.root)["source_branch"])

    def test_human_candidate_requires_independent_review_and_fixed_acceptance(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, implementer="human")
        (self.root / "pom.xml").write_text("<project><version>2</version></project>\n", encoding="utf-8")
        git(self.root, "add", "pom.xml")
        git(self.root, "commit", "-q", "-m", "base: update pom")
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")
        git(self.root, "config", "user.name", "Independent Reviewer")
        base_execution.record_base_review(self.root, change_id, "pass", "已核对底座修改")
        self.assertEqual(run_change(self.root, change_id)["status"], "MANUAL_ACCEPTANCE_REQUIRED")
        record_manual_acceptance(self.root, change_id, ["BASE-ACCEPT"], "构建配置符合目标")
        self.assertEqual(run_change(self.root, change_id)["status"], "FINALIZING")
        self.assertEqual(git(self.root, "branch", "--show-current"),
                         load_baseline(self.root)["source_branch"])
        report = load_json(self.root / "reports/storyloop" / change_id / "report.json")
        self.assertEqual(report["review"]["reviewer"], "Independent Reviewer")
        self.assertEqual(report["manual_acceptance"][0]["criterion_id"], "BASE-ACCEPT")
        self.assertEqual(report["build_note"], "项目声明无构建步骤")

    def test_cli_records_named_base_review(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, implementer="human")
        (self.root / "pom.xml").write_text("<project/>\n<!-- base -->\n", encoding="utf-8")
        git(self.root, "add", "pom.xml")
        git(self.root, "commit", "-q", "-m", "base")
        run_change(self.root, change_id)
        git(self.root, "config", "user.name", "Independent Reviewer")
        args = parser().parse_args(["base-review", str(self.root), "--change", change_id,
                                    "--verdict", "pass", "--note", "已复核"])
        self.assertEqual(dispatch(args)["status"], "REVIEW_RECORDED")

    def test_review_stop_resumes_to_manual_acceptance(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, implementer="human")
        (self.root / "pom.xml").write_text("<project/>\n<!-- reviewed -->\n", encoding="utf-8")
        git(self.root, "add", "pom.xml")
        git(self.root, "commit", "-q", "-m", "base")
        run_change(self.root, change_id)
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")
        git(self.root, "config", "user.name", "Independent Reviewer")
        base_execution.record_base_review(self.root, change_id, "pass", "已复核")
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "MANUAL_ACCEPTANCE_REQUIRED")

    def test_review_record_rejects_dirty_workspace_and_diagnoses_stale_digest(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, implementer="human")
        (self.root / "pom.xml").write_text("<project><version>2</version></project>\n",
                                             encoding="utf-8")
        git(self.root, "add", "pom.xml")
        git(self.root, "commit", "-q", "-m", "candidate")
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")
        git(self.root, "config", "user.name", "Independent Reviewer")
        note = self.root / "notes.txt"
        note.write_text("review notes\n", encoding="utf-8")
        with self.assertRaisesRegex(StoryLoopError, "worktree"):
            base_execution.record_base_review(self.root, change_id, "pass", "已核对")
        note.unlink()
        base_execution.record_base_review(self.root, change_id, "pass", "已核对")
        path = self.root / ".storyloop/state/base-reviews" / f"{change_id}.json"
        review = load_json(path)
        review["candidate_sha256"] = "0" * 64
        write_json(path, review)
        with self.assertRaisesRegex(StoryLoopError, "review.*candidate"):
            run_change(self.root, change_id)
        base_execution.record_base_review(self.root, change_id, "pass", "已重新核对")
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "MANUAL_ACCEPTANCE_REQUIRED")

    def test_approved_commands_cannot_change_before_old_green(self):
        config = load_project_config(self.root)
        config["profile"]["unit_commands"] = [[sys.executable, "-c", "print('changed')"]]
        write_json(self.root / ".storyloop/config.json", config)
        with self.assertRaisesRegex(StoryLoopError, "commands"):
            run_change(self.root, self.change["change_id"], implementer="human")

    def test_review_record_cannot_claim_implementation_author(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, implementer="human")
        (self.root / "pom.xml").write_text("<project/>\n<!-- reviewed -->\n", encoding="utf-8")
        git(self.root, "add", "pom.xml")
        git(self.root, "commit", "-q", "-m", "base")
        run_change(self.root, change_id)
        git(self.root, "config", "user.name", "Independent Reviewer")
        base_execution.record_base_review(self.root, change_id, "pass", "已复核")
        path = self.root / ".storyloop/state/base-reviews" / f"{change_id}.json"
        review = load_json(path)
        review["reviewer"] = git(self.root, "show", "-s", "--format=%an", "HEAD")
        write_json(path, review)
        with self.assertRaisesRegex(StoryLoopError, "reviewer"):
            run_change(self.root, change_id)

    def test_review_rejects_author_of_earlier_candidate_commit(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, implementer="human")
        (self.root / "pom.xml").write_text("<project><version>2</version></project>\n", encoding="utf-8")
        git(self.root, "add", "pom.xml")
        git(self.root, "commit", "-q", "-m", "substantive change")
        git(self.root, "config", "user.name", "Secondary Author")
        (self.root / "pom.xml").write_text("<project><version>2</version><!-- note --></project>\n",
                                            encoding="utf-8")
        git(self.root, "add", "pom.xml")
        git(self.root, "commit", "-q", "-m", "minor note")
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")
        git(self.root, "config", "user.name", "StoryLoop Test")
        with self.assertRaisesRegex(StoryLoopError, "reviewer"):
            base_execution.record_base_review(self.root, change_id, "pass", "已核对")

    def test_review_rejects_candidate_committer_even_when_author_differs(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, implementer="human")
        (self.root / "pom.xml").write_text("<project><version>2</version></project>\n",
                                             encoding="utf-8")
        git(self.root, "add", "pom.xml")
        git(self.root, "commit", "-q", "-m", "initial candidate")
        git(self.root, "config", "user.name", "Independent Reviewer")
        git(self.root, "commit", "--amend", "-q", "--no-edit")
        self.assertNotEqual(git(self.root, "show", "-s", "--format=%an", "HEAD"),
                            git(self.root, "show", "-s", "--format=%cn", "HEAD"))
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")
        with self.assertRaisesRegex(StoryLoopError, "reviewer"):
            base_execution.record_base_review(self.root, change_id, "pass", "已核对")

    def test_g2_rechecks_proposal_scope_after_review(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, implementer="human")
        (self.root / "pom.xml").write_text("<project/>\n<!-- scope -->\n", encoding="utf-8")
        git(self.root, "add", "pom.xml")
        git(self.root, "commit", "-q", "-m", "base")
        run_change(self.root, change_id)
        git(self.root, "config", "user.name", "Independent Reviewer")
        base_execution.record_base_review(self.root, change_id, "pass", "已复核")
        run_change(self.root, change_id)
        record_manual_acceptance(self.root, change_id, ["BASE-ACCEPT"], "目标满足")
        path = self.root / ".storyloop/base-proposals" / f"{change_id}.json"
        proposal = load_json(path)
        proposal["changes"][0]["category"] = "UPSTREAM_MUTABLE"
        write_json(path, proposal)
        with self.assertRaisesRegex(StoryLoopError, "scope"):
            run_change(self.root, change_id)

    def test_rejected_review_reopens_human_implementation(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, implementer="human")
        (self.root / "pom.xml").write_text("<project><version>2</version></project>\n", encoding="utf-8")
        git(self.root, "add", "pom.xml")
        git(self.root, "commit", "-q", "-m", "first candidate")
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")
        git(self.root, "config", "user.name", "Independent Reviewer")
        base_execution.record_base_review(self.root, change_id, "fail", "需要修订")
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")
        git(self.root, "config", "user.name", "StoryLoop Test")
        (self.root / "pom.xml").write_text("<project><version>3</version></project>\n", encoding="utf-8")
        git(self.root, "add", "pom.xml")
        git(self.root, "commit", "-q", "-m", "revised candidate")
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")

    def test_report_commit_crash_resumes_without_revalidating_report_paths(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, implementer="human")
        (self.root / "pom.xml").write_text("<project><version>2</version></project>\n", encoding="utf-8")
        git(self.root, "add", "pom.xml")
        git(self.root, "commit", "-q", "-m", "candidate")
        run_change(self.root, change_id)
        git(self.root, "config", "user.name", "Independent Reviewer")
        base_execution.record_base_review(self.root, change_id, "pass", "已核对")
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "MANUAL_ACCEPTANCE_REQUIRED")
        record_manual_acceptance(self.root, change_id, ["BASE-ACCEPT"], "满足目标")
        original = base_execution.save_run

        def fail_after_report(project, state):
            if state["status"] == "VERIFIED":
                raise RuntimeError("crash after report commit")
            return original(project, state)

        with mock.patch.object(base_execution, "save_run", side_effect=fail_after_report):
            with self.assertRaisesRegex(RuntimeError, "crash after report commit"):
                run_change(self.root, change_id)
        report_head = git(self.root, "rev-parse", "HEAD")
        self.assertEqual(run_change(self.root, change_id)["status"], "FINALIZING")
        self.assertEqual(git(self.root, "show", "--format=%s", "--no-patch", report_head),
                         f"docs({change_id}): record base verification")

    def test_report_files_written_before_commit_are_recovered(self):
        change_id = self._accepted_candidate()
        original = base_execution.commit_paths

        def fail_before_report_commit(project, paths, message, **kwargs):
            if message.startswith("docs("):
                raise RuntimeError("crash before report commit")
            return original(project, paths, message, **kwargs)

        with mock.patch.object(base_execution, "commit_paths", side_effect=fail_before_report_commit):
            with self.assertRaisesRegex(RuntimeError, "crash before report commit"):
                run_change(self.root, change_id)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"),
                         load_json(self.root / ".storyloop/state/runs" / f"{change_id}.json")
                         ["candidate_commit"])
        self.assertEqual(run_change(self.root, change_id)["status"], "FINALIZING")

    def test_dirty_report_commit_recovery_preserves_manual_acceptance_state(self):
        change_id = self._accepted_candidate()
        original = base_execution.save_run

        def fail_after_report(project, state):
            if state["status"] == "VERIFIED":
                raise RuntimeError("crash after report commit")
            return original(project, state)

        with mock.patch.object(base_execution, "save_run", side_effect=fail_after_report):
            with self.assertRaisesRegex(RuntimeError, "crash after report commit"):
                run_change(self.root, change_id)
        (self.root / "notes.txt").write_text("unrelated\n", encoding="utf-8")
        with self.assertRaisesRegex(StoryLoopError, "worktree"):
            run_change(self.root, change_id)
        state_path = self.root / ".storyloop/state/runs" / f"{change_id}.json"
        self.assertEqual(load_json(state_path)["status"], "MANUAL_ACCEPTANCE_REQUIRED")
        (self.root / "notes.txt").unlink()
        self.assertEqual(run_change(self.root, change_id)["status"], "FINALIZING")

    def test_contract_commit_crash_recovers_with_prepared_base_file_dirty(self):
        change_id = self.change["change_id"]
        (self.root / "pom.xml").write_text("<project><version>2</version></project>\n", encoding="utf-8")
        original = base_execution.save_run

        def fail_after_contract(project, state):
            if state["status"] == "CONTRACT_COMMITTED":
                raise RuntimeError("crash after contract commit")
            return original(project, state)

        with mock.patch.object(base_execution, "save_run", side_effect=fail_after_contract):
            with self.assertRaisesRegex(RuntimeError, "crash after contract commit"):
                run_change(self.root, change_id, implementer="human")
        contract_head = git(self.root, "rev-parse", "HEAD")
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), contract_head)

    def test_branch_creation_crash_before_first_state_save_recovers(self):
        change_id = self.change["change_id"]
        with mock.patch.object(base_execution, "save_run", side_effect=RuntimeError("branch crash")):
            with self.assertRaisesRegex(RuntimeError, "branch crash"):
                run_change(self.root, change_id, implementer="human")
        self.assertEqual(git(self.root, "branch", "--show-current"), f"storyloop/{change_id}")
        self.assertEqual(run_change(self.root, change_id, implementer="human")["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")

    def test_contract_files_written_before_commit_are_recovered(self):
        change_id = self.change["change_id"]
        with mock.patch.object(base_execution, "commit_paths", side_effect=RuntimeError("contract crash")):
            with self.assertRaisesRegex(RuntimeError, "contract crash"):
                run_change(self.root, change_id, implementer="human")
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")

    def test_old_green_evidence_before_stop_save_recovers(self):
        change_id = self.change["change_id"]
        original = base_execution.save_run

        def fail_after_green(project, state):
            if state["status"] == "IMPLEMENTING":
                raise RuntimeError("old green crash")
            return original(project, state)

        with mock.patch.object(base_execution, "save_run", side_effect=fail_after_green):
            with self.assertRaisesRegex(RuntimeError, "old green crash"):
                run_change(self.root, change_id, implementer="human")
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")

    def test_candidate_evidence_before_review_state_save_is_reused(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, implementer="human")
        (self.root / "pom.xml").write_text("<project><version>2</version></project>\n",
                                             encoding="utf-8")
        git(self.root, "add", "pom.xml")
        git(self.root, "commit", "-q", "-m", "candidate")
        original = base_execution.transition

        def fail_before_review(*args, **kwargs):
            if kwargs.get("event") == "human_candidate_ready":
                raise RuntimeError("candidate evidence crash")
            return original(*args, **kwargs)

        with mock.patch.object(base_execution, "transition", side_effect=fail_before_review):
            with self.assertRaisesRegex(RuntimeError, "candidate evidence crash"):
                run_change(self.root, change_id)
        with mock.patch.object(base_execution, "run_commands_isolated",
                               side_effect=AssertionError("candidate checks reran")):
            self.assertEqual(run_change(self.root, change_id)["status"],
                             "BASE_REVIEW_REQUIRED")

    def test_implementing_checkpoint_reuses_matching_candidate_evidence(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, implementer="human")
        (self.root / "pom.xml").write_text("<project><version>2</version></project>\n",
                                             encoding="utf-8")
        git(self.root, "add", "pom.xml")
        git(self.root, "commit", "-q", "-m", "candidate")
        run_change(self.root, change_id)
        path = self.root / ".storyloop/state/runs" / f"{change_id}.json"
        state = load_json(path)
        state["status"] = "IMPLEMENTING"
        write_json(path, state)
        with mock.patch.object(base_execution, "run_commands_isolated",
                               side_effect=AssertionError("matching checks reran")):
            self.assertEqual(run_change(self.root, change_id)["status"],
                             "BASE_REVIEW_REQUIRED")

    def test_merge_commit_before_merged_state_save_is_adopted(self):
        change_id = self._accepted_candidate()
        original = base_execution.save_run

        def fail_after_merge(project, state):
            if state["status"] == "MERGED":
                raise RuntimeError("merge state crash")
            return original(project, state)

        with mock.patch.object(base_execution, "save_run", side_effect=fail_after_merge):
            with self.assertRaisesRegex(RuntimeError, "merge state crash"):
                run_change(self.root, change_id)
        self.assertEqual(run_change(self.root, change_id)["status"], "FINALIZING")

    def test_receipt_commit_before_finalizing_state_save_is_adopted(self):
        change_id = self._accepted_candidate()
        original = base_execution.save_run

        def fail_after_receipt(project, state):
            if state["status"] == "FINALIZING":
                raise RuntimeError("receipt state crash")
            return original(project, state)

        with mock.patch.object(base_execution, "save_run", side_effect=fail_after_receipt):
            with self.assertRaisesRegex(RuntimeError, "receipt state crash"):
                run_change(self.root, change_id)
        self.assertEqual(run_change(self.root, change_id)["status"], "FINALIZING")
        self.assertEqual(run_change(self.root, change_id)["status"], "PASSED")


if __name__ == "__main__":
    unittest.main()
