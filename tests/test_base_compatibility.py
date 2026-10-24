"""A base run advances once; legacy proposals enter the same approval route."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from storyloop import base_upgrade
from storyloop.approvals import approve_change
from storyloop.baseline import load_baseline
from storyloop.manual_acceptance import record_manual_acceptance
from storyloop.project_boundaries import _write_base_proposal
from storyloop.onboarding_cli import bootstrap_with_project_checks
from storyloop.project_loop import run_change
from storyloop.storage import load_json
from storyloop.storage import write_json
from storyloop.models import StoryLoopError
from tests.test_baseline_behavior import git, repository


class BaseCompatibilityTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        repository(self.root)
        smoke = self.root / "acceptance/smoke"
        smoke.mkdir(parents=True)
        (smoke / "check.py").write_text("print('green')\n", encoding="utf-8")
        (smoke / "manifest.json").write_text(
            '{"version":1,"tests":[{"id":"check","description":"check",'
            '"command":["{python}","acceptance/smoke/check.py"],"timeout_seconds":30}]}',
            encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "smoke")
        bootstrap_with_project_checks(self.root, {
            "unit_commands": [[sys.executable, "-c", "print('green')"]],
            "integration_commands": [], "check_commands": [], "base_build_commands": [],
        })
        self.before = load_baseline(self.root)

    def _merged_candidate(self):
        proposed = base_upgrade.propose_base_change(self.root, ["pom.xml"], "刷新构建配置")
        change_id = proposed["change_id"]
        approve_change(self.root, change_id)
        run_change(self.root, change_id, implementer="human")
        (self.root / "pom.xml").write_text("<project/>\n<!-- upgraded -->\n", encoding="utf-8")
        git(self.root, "add", "pom.xml")
        git(self.root, "commit", "-q", "-m", "base")
        run_change(self.root, change_id)
        git(self.root, "config", "user.name", "Independent Reviewer")
        from storyloop.base_execution import record_base_review
        record_base_review(self.root, change_id, "pass", "已复核")
        run_change(self.root, change_id)
        record_manual_acceptance(self.root, change_id, ["BASE-ACCEPT"], "目标满足")
        self.assertEqual(run_change(self.root, change_id)["status"], "FINALIZING")
        return change_id

    def test_finalizing_advances_baseline_once_and_resumes(self):
        change_id = self._merged_candidate()
        first = run_change(self.root, change_id)
        self.assertEqual(first["status"], "PASSED")
        baseline = load_baseline(self.root)
        self.assertEqual(baseline["baseline_version"], "1.0.1")
        self.assertEqual(baseline["source_head"], git(self.root, "rev-parse", "HEAD"))
        self.assertEqual(run_change(self.root, change_id)["status"], "PASSED")
        self.assertEqual(load_baseline(self.root), baseline)
        receipt = load_json(self.root / ".storyloop/state/base-upgrades" / f"{change_id}.json")
        self.assertEqual(receipt["status"], "ACCEPTED")

    def test_finalizing_rejects_baseline_older_than_approved_version(self):
        change_id = self._merged_candidate()
        path = self.root / ".storyloop/baseline.json"
        baseline = load_json(path)
        write_json(path, {**baseline, "baseline_version": "0.9.9"})
        result = run_change(self.root, change_id)
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")
        self.assertIn("baseline", result["next"])
        write_json(path, baseline)
        self.assertEqual(run_change(self.root, change_id)["status"], "PASSED")

    def test_finalizing_baseline_mismatch_does_not_offer_reset(self):
        change_id = self._merged_candidate()
        path = self.root / ".storyloop/baseline.json"
        baseline = load_json(path)
        write_json(path, {**baseline, "baseline_version": "0.9.9"})
        result = run_change(self.root, change_id)
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")
        self.assertNotIn("reset", str(result["stop"]["options"]))

    def test_finalizing_checks_ancestry_without_listing_history(self):
        change_id = self._merged_candidate()
        from storyloop import base_finalization
        original = base_finalization._git

        def bounded_git(project, *arguments):
            if arguments and arguments[0] == "rev-list":
                raise AssertionError("unbounded history enumeration")
            return original(project, *arguments)

        with patch.object(base_finalization, "_git", side_effect=bounded_git):
            self.assertEqual(run_change(self.root, change_id)["status"], "PASSED")

    def test_unbound_finalization_receipt_cannot_choose_new_version(self):
        change_id = self._merged_candidate()
        from storyloop import base_finalization
        from storyloop.baseline import load_project_config
        state = load_json(self.root / ".storyloop/state/runs" / f"{change_id}.json")
        change = load_json(self.root / ".storyloop/state/changes" / f"{change_id}.json")
        baseline = load_baseline(self.root)
        config = load_project_config(self.root)
        head = state["integration_commit"]
        receipt = base_finalization._new_receipt(
            self.root, change, state, baseline, config, head,
            self.root / ".storyloop/baseline.json", self.root / ".storyloop/config.json")
        receipt["new_baseline_version"] = "9.9.9"
        candidate = base_upgrade._next_baseline(self.root, baseline, config["profile"], head,
                                                "9.9.9")
        receipt["candidate_baseline_digest"] = base_upgrade._dict_digest(candidate)
        path = self.root / ".storyloop/state/base-upgrades" / f"{change_id}.json"
        write_json(path, receipt)
        self.assertEqual(run_change(self.root, change_id)["status"], "PASSED")
        self.assertEqual(load_baseline(self.root)["baseline_version"], "1.0.1")

    def test_premature_accepted_receipt_is_discarded_before_baseline_write(self):
        change_id = self._merged_candidate()
        from storyloop import base_finalization
        from storyloop.baseline import load_project_config
        state = load_json(self.root / ".storyloop/state/runs" / f"{change_id}.json")
        change = load_json(self.root / ".storyloop/state/changes" / f"{change_id}.json")
        baseline = load_baseline(self.root)
        receipt = base_finalization._new_receipt(
            self.root, change, state, baseline, load_project_config(self.root),
            state["integration_commit"], self.root / ".storyloop/baseline.json",
            self.root / ".storyloop/config.json")
        receipt["status"] = "ACCEPTED"
        path = self.root / ".storyloop/state/base-upgrades" / f"{change_id}.json"
        write_json(path, receipt)
        self.assertEqual(run_change(self.root, change_id)["status"], "PASSED")
        self.assertEqual(load_baseline(self.root)["baseline_version"], "1.0.1")

    def test_legacy_finalizing_state_after_baseline_write_is_revalidated(self):
        change_id = self._merged_candidate()
        from storyloop import base_finalization
        original = base_finalization.write_json

        def interrupt(path, value, **kwargs):
            original(path, value, **kwargs)
            if path.name == "baseline.json" and value["baseline_version"] == "1.0.1":
                raise RuntimeError("baseline write crash")

        with patch.object(base_finalization, "write_json", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "baseline write crash"):
                run_change(self.root, change_id)
        state_path = self.root / ".storyloop/state/runs" / f"{change_id}.json"
        state = load_json(state_path)
        state.pop("base_finalization_receipt")
        state.pop("base_finalization_receipt_sha256")
        write_json(state_path, state)
        self.assertEqual(run_change(self.root, change_id)["status"], "PASSED")

    def test_legacy_applied_receipt_can_recover_after_old_baseline_is_restored(self):
        change_id = self._merged_candidate()
        from storyloop import base_finalization
        baseline_path = self.root / ".storyloop/baseline.json"
        old_bytes = baseline_path.read_bytes()
        original = base_finalization.write_json

        def crash_after_baseline(path, value, **kwargs):
            original(path, value, **kwargs)
            if path == baseline_path:
                raise RuntimeError("baseline write interrupted")

        with patch.object(base_finalization, "write_json", side_effect=crash_after_baseline):
            with self.assertRaisesRegex(RuntimeError, "baseline write interrupted"):
                run_change(self.root, change_id)
        state_path = self.root / ".storyloop/state/runs" / f"{change_id}.json"
        state = load_json(state_path)
        state.pop("base_finalization_receipt")
        state.pop("base_finalization_receipt_sha256")
        write_json(state_path, state)
        with patch.object(base_finalization, "_load_or_repair_receipt",
                          side_effect=RuntimeError("receipt interrupted")):
            with self.assertRaisesRegex(RuntimeError, "receipt interrupted"):
                run_change(self.root, change_id)
        baseline_path.write_bytes(old_bytes)
        self.assertEqual(run_change(self.root, change_id)["status"], "PASSED")
        self.assertEqual(load_baseline(self.root)["baseline_version"], "1.0.1")

    def test_proposed_base_update_imports_without_changing_old_file(self):
        proposed = base_upgrade.propose_base_change(self.root, ["pom.xml"], "刷新构建配置")
        path = Path(proposed["proposal"])
        before = path.read_bytes()
        result = base_upgrade.upgrade_baseline(self.root, proposed["change_id"])
        self.assertEqual(result["status"], "APPROVAL_REQUIRED")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(base_upgrade.upgrade_baseline(self.root, proposed["change_id"])["status"],
                         "APPROVAL_REQUIRED")
        self.assertEqual(load_baseline(self.root), self.before)

    def test_crash_after_baseline_write_recovers_without_second_increment(self):
        change_id = self._merged_candidate()
        from storyloop import base_finalization
        original = base_finalization.write_json
        crashed = False

        def interrupt_after_baseline(path, value, **kwargs):
            nonlocal crashed
            original(path, value, **kwargs)
            if path.name == "baseline.json" and not crashed:
                crashed = True
                raise StoryLoopError("simulated crash after baseline write")

        with patch("storyloop.base_finalization.write_json", side_effect=interrupt_after_baseline):
            with self.assertRaisesRegex(StoryLoopError, "simulated crash"):
                run_change(self.root, change_id)
        self.assertEqual(load_baseline(self.root)["baseline_version"], "1.0.1")
        self.assertEqual(run_change(self.root, change_id)["status"], "PASSED")
        self.assertEqual(load_baseline(self.root)["baseline_version"], "1.0.1")

    def test_crash_after_accepting_receipt_write_resumes_finalization(self):
        change_id = self._merged_candidate()
        from storyloop import base_finalization
        original = base_finalization.write_json

        def interrupt(path, value, **kwargs):
            original(path, value, **kwargs)
            if path.name == f"{change_id}.json" and value.get("status") == "ACCEPTING":
                raise RuntimeError("accepting receipt crash")

        with patch("storyloop.base_finalization.write_json", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "accepting receipt crash"):
                run_change(self.root, change_id)
        self.assertEqual(run_change(self.root, change_id)["status"], "PASSED")
        self.assertEqual(load_baseline(self.root)["baseline_version"], "1.0.1")

    def test_crash_after_accepted_receipt_write_does_not_increment_again(self):
        change_id = self._merged_candidate()
        from storyloop import base_finalization
        original = base_finalization.write_json

        def interrupt(path, value, **kwargs):
            original(path, value, **kwargs)
            if path.name == f"{change_id}.json" and value.get("status") == "ACCEPTED":
                raise RuntimeError("accepted receipt crash")

        with patch("storyloop.base_finalization.write_json", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "accepted receipt crash"):
                run_change(self.root, change_id)
        self.assertEqual(run_change(self.root, change_id)["status"], "PASSED")
        self.assertEqual(load_baseline(self.root)["baseline_version"], "1.0.1")

    def test_repeated_import_rejects_changed_legacy_proposal(self):
        proposed = base_upgrade.propose_base_change(self.root, ["pom.xml"], "刷新构建配置")
        path = Path(proposed["proposal"])
        proposal = load_json(path)
        proposal["changes"][0]["path"] = "README.md"
        write_json(path, proposal)
        with self.assertRaisesRegex(StoryLoopError, "proposal.*changed"):
            base_upgrade.upgrade_baseline(self.root, proposed["change_id"])

    def test_special_base_scopes_require_their_own_directories(self):
        skills = base_upgrade.propose_base_change(
            self.root, [".codex/skills/example/SKILL.md"], "安装技能", scope="skills")
        knowledge = base_upgrade.propose_base_change(
            self.root, ["openspec/foundation/map.md"], "补底座知识", scope="knowledge")
        for proposed, scope in ((skills, "skills"), (knowledge, "knowledge")):
            state = load_json(self.root / ".storyloop/state/changes" / f"{proposed['change_id']}.json")
            self.assertEqual(state["product"]["base"]["scope"], scope)
        with self.assertRaisesRegex(StoryLoopError, "scope"):
            base_upgrade.propose_base_change(self.root, ["README.md"], "错误范围", scope="skills")

    def test_receipting_retry_adopts_existing_receipt_commit(self):
        change_id = self._merged_candidate()
        state_path = self.root / ".storyloop/state/runs" / f"{change_id}.json"
        state = load_json(state_path)
        integration = state["integration_commit"]
        state["status"] = "RECEIPTING"
        state.pop("integration_commit")
        write_json(state_path, state)
        self.assertEqual(run_change(self.root, change_id)["status"], "FINALIZING")
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), integration)

    def test_base_update_reads_completed_lifecycle_receipt(self):
        change_id = self._merged_candidate()
        run_change(self.root, change_id)
        result = base_upgrade.upgrade_baseline(self.root, change_id)
        self.assertEqual(result["status"], "BASELINE_UPGRADED")
        self.assertEqual(result["baseline_version"], "1.0.1")

    def test_prepared_candidate_moves_to_base_branch_without_source_commit(self):
        proposed = base_upgrade.propose_base_change(self.root, ["pom.xml"], "刷新构建配置")
        change_id = proposed["change_id"]
        approve_change(self.root, change_id)
        source_head = git(self.root, "rev-parse", "HEAD")
        (self.root / "pom.xml").write_text("<project/>\n<!-- prepared -->\n", encoding="utf-8")
        result = run_change(self.root, change_id, implementer="human")
        self.assertEqual(result["status"], "HUMAN_IMPLEMENTATION_REQUIRED")
        self.assertEqual(git(self.root, "rev-parse", "HEAD"),
                         load_json(self.root / ".storyloop/state/runs" / f"{change_id}.json")["contract_commit"])
        self.assertEqual(git(self.root, "rev-parse", self.before["source_branch"]), source_head)
        self.assertIn("pom.xml", git(self.root, "status", "--porcelain=v1"))

    def test_base_path_set_includes_both_sides_of_detected_rename(self):
        (self.root / "a.md").write_text("same content\n", encoding="utf-8")
        git(self.root, "add", "a.md")
        git(self.root, "commit", "-q", "-m", "add source")
        source = git(self.root, "rev-parse", "HEAD")
        git(self.root, "config", "diff.renames", "true")
        git(self.root, "mv", "a.md", "b.md")
        git(self.root, "commit", "-q", "-m", "rename source")
        self.assertEqual(base_upgrade._changed_since(self.root, source), {"a.md", "b.md"})

    def test_agent_generated_ci_protected_proposal_requires_new_human_authorization(self):
        path = _write_base_proposal(
            self.root, "FEAT-20261024-001",
            [{"path": ".github/workflows/ci.yml", "category": "CI_PROTECTED"}], self.before)
        proposal_bytes = path.read_bytes()
        change_id = path.stem
        with self.assertRaisesRegex(StoryLoopError, "base-propose --allow-ci"):
            base_upgrade.upgrade_baseline(self.root, change_id)
        self.assertFalse((self.root / ".storyloop/state/changes" / f"{change_id}.json").exists())
        self.assertEqual(path.read_bytes(), proposal_bytes)

    def test_previously_imported_agent_ci_proposal_cannot_start_base_branch(self):
        path = _write_base_proposal(
            self.root, "FEAT-20261024-001",
            [{"path": ".github/workflows/ci.yml", "category": "CI_PROTECTED"}], self.before)
        change_id = path.stem
        base_upgrade.draft_from_proposal(self.root, change_id, scope="ci")
        approve_change(self.root, change_id)
        (self.root / ".github/workflows/ci.yml").write_text("name: reviewed\n", encoding="utf-8")
        with self.assertRaisesRegex(StoryLoopError, "撤回.*base-propose --allow-ci"):
            run_change(self.root, change_id, implementer="human")
        self.assertFalse((self.root / ".storyloop/state/runs" / f"{change_id}.json").exists())
        self.assertEqual(git(self.root, "branch", "--show-current"), self.before["source_branch"])

    def _oracle_candidate(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        project = Path(folder.name)
        repository(project)
        smoke = project / "acceptance/smoke"
        smoke.mkdir(parents=True)
        (smoke / "check.py").write_text("print('green')\n", encoding="utf-8")
        (smoke / "manifest.json").write_text(
            '{"version":1,"tests":[{"id":"check","description":"check",'
            '"command":["{python}","acceptance/smoke/check.py"],"timeout_seconds":30}]}',
            encoding="utf-8")
        skill = project / ".claude/skills/acceptance/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nroles: [acceptance]\n---\nold\n", encoding="utf-8")
        git(project, "add", ".")
        git(project, "commit", "-q", "-m", "oracle skill")
        bootstrap_with_project_checks(project, {
            "unit_commands": [[sys.executable, "-c", "pass"]],
            "integration_commands": [], "check_commands": [], "base_build_commands": [],
        })
        proposed = base_upgrade.propose_base_change(
            project, [".claude/skills/acceptance/SKILL.md"], "更新 oracle 技能", scope="skills")
        change_id = proposed["change_id"]
        approve_change(project, change_id)
        run_change(project, change_id, implementer="human")
        skill.write_text("---\nroles: [acceptance]\n---\nnew\n", encoding="utf-8")
        git(project, "add", ".claude/skills/acceptance/SKILL.md")
        git(project, "commit", "-q", "-m", "human oracle update")
        return project, change_id

    def test_oracle_skill_candidate_needs_explicit_confirmation(self):
        project, change_id = self._oracle_candidate()
        result = run_change(project, change_id)
        self.assertEqual(result["status"], "HUMAN_IMPLEMENTATION_REQUIRED")
        self.assertIn("confirm-oracle-skills", result["reason"])
        from storyloop.oracle_skill_approval import CONFIRMED
        token = CONFIRMED.set(True)
        try:
            self.assertEqual(run_change(project, change_id)["status"], "BASE_REVIEW_REQUIRED")
        finally:
            CONFIRMED.reset(token)

    def test_unbound_oracle_evidence_crash_requires_reconfirmation_and_reaches_report(self):
        from storyloop import base_execution
        from storyloop.oracle_skill_approval import CONFIRMED
        project, change_id = self._oracle_candidate()
        original = base_execution.transition

        def interrupt(*args, **kwargs):
            if kwargs.get("event") == "human_candidate_ready":
                raise RuntimeError("evidence crash")
            return original(*args, **kwargs)

        token = CONFIRMED.set(True)
        try:
            with patch.object(base_execution, "transition", side_effect=interrupt):
                with self.assertRaisesRegex(RuntimeError, "evidence crash"):
                    run_change(project, change_id)
        finally:
            CONFIRMED.reset(token)
        evidence = load_json(project / ".storyloop/evidence" / change_id /
                             "base-candidate-green.json")
        approval = evidence["oracle_skills_approval"]
        self.assertEqual(approval["candidate_commit"], git(project, "rev-parse", "HEAD"))
        self.assertEqual(run_change(project, change_id)["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")
        result = run_change(project, change_id)
        self.assertEqual(result["status"], "HUMAN_IMPLEMENTATION_REQUIRED")
        self.assertIn("confirm-oracle-skills", result["reason"])
        token = CONFIRMED.set(True)
        try:
            self.assertEqual(run_change(project, change_id)["status"], "BASE_REVIEW_REQUIRED")
        finally:
            CONFIRMED.reset(token)
        git(project, "config", "user.name", "Independent Reviewer")
        base_execution.record_base_review(project, change_id, "pass", "已核对")
        self.assertEqual(run_change(project, change_id)["status"], "MANUAL_ACCEPTANCE_REQUIRED")
        record_manual_acceptance(project, change_id, ["BASE-ACCEPT"], "目标满足")
        self.assertEqual(run_change(project, change_id)["status"], "FINALIZING")
        report = load_json(project / "reports/storyloop" / change_id / "report.json")
        self.assertEqual(report["oracle_skills_approval"], approval)
        self.assertIn("Oracle 技能确认", (project / "reports/storyloop" / change_id /
                                    "report.md").read_text(encoding="utf-8"))

    def test_tampered_oracle_evidence_requires_new_confirmation_and_review(self):
        from storyloop import base_execution
        from storyloop.oracle_skill_approval import CONFIRMED
        project, change_id = self._oracle_candidate()
        token = CONFIRMED.set(True)
        try:
            self.assertEqual(run_change(project, change_id)["status"], "BASE_REVIEW_REQUIRED")
        finally:
            CONFIRMED.reset(token)
        git(project, "config", "user.name", "Independent Reviewer")
        base_execution.record_base_review(project, change_id, "pass", "已核对")
        run_change(project, change_id)
        record_manual_acceptance(project, change_id, ["BASE-ACCEPT"], "目标满足")
        evidence_path = project / ".storyloop/evidence" / change_id / "base-candidate-green.json"
        evidence = load_json(evidence_path)
        evidence.pop("oracle_skills_approval", None)
        write_json(evidence_path, evidence)
        self.assertEqual(run_change(project, change_id)["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")
        self.assertFalse(evidence_path.exists())
        token = CONFIRMED.set(True)
        try:
            self.assertEqual(run_change(project, change_id)["status"], "BASE_REVIEW_REQUIRED")
        finally:
            CONFIRMED.reset(token)
        base_execution.record_base_review(project, change_id, "pass", "重新审查")
        self.assertEqual(run_change(project, change_id)["status"], "MANUAL_ACCEPTANCE_REQUIRED")
        record_manual_acceptance(project, change_id, ["BASE-ACCEPT"], "重新验收")
        self.assertEqual(run_change(project, change_id)["status"], "FINALIZING")

    def test_legacy_source_commit_requires_explicit_replay_before_starting_run(self):
        proposed = base_upgrade.propose_base_change(self.root, ["pom.xml"], "旧底座候选")
        change_id = proposed["change_id"]
        (self.root / "pom.xml").write_text("<project/>\n<!-- old candidate -->\n", encoding="utf-8")
        git(self.root, "add", "pom.xml")
        git(self.root, "commit", "-q", "-m", "old source candidate")
        old_candidate = git(self.root, "rev-parse", "HEAD")
        base_upgrade.upgrade_baseline(self.root, change_id)
        approve_change(self.root, change_id)
        with self.assertRaisesRegex(StoryLoopError, "replay.*base branch"):
            run_change(self.root, change_id, implementer="human")
        self.assertFalse((self.root / ".storyloop/state/runs" / f"{change_id}.json").exists())
        self.assertFalse(git(self.root, "branch", "--list", f"storyloop/{change_id}"))
        git(self.root, "revert", "--no-edit", old_candidate)
        self.assertEqual(run_change(self.root, change_id, implementer="human")["status"],
                         "HUMAN_IMPLEMENTATION_REQUIRED")
        git(self.root, "cherry-pick", "-n", old_candidate)
        git(self.root, "commit", "-q", "-m", "replayed candidate")
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")
        git(self.root, "config", "user.name", "Independent Reviewer")
        from storyloop.base_execution import record_base_review
        record_base_review(self.root, change_id, "pass", "已复核重放候选")
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "MANUAL_ACCEPTANCE_REQUIRED")
        record_manual_acceptance(self.root, change_id, ["BASE-ACCEPT"], "目标满足")
        self.assertEqual(run_change(self.root, change_id)["status"], "FINALIZING")
        self.assertEqual(run_change(self.root, change_id)["status"], "PASSED")
        self.assertEqual(load_baseline(self.root)["baseline_version"], "1.0.1")


if __name__ == "__main__":
    unittest.main()
