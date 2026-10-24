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
        with self.assertRaisesRegex(StoryLoopError, "base-propose --allow-ci"):
            run_change(self.root, change_id, implementer="human")
        self.assertFalse((self.root / ".storyloop/state/runs" / f"{change_id}.json").exists())
        self.assertEqual(git(self.root, "branch", "--show-current"), self.before["source_branch"])

    def test_oracle_skill_candidate_needs_explicit_confirmation(self):
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
        with self.assertRaisesRegex(StoryLoopError, "confirm-oracle-skills"):
            run_change(project, change_id)
        from storyloop.oracle_skill_approval import CONFIRMED
        token = CONFIRMED.set(True)
        try:
            self.assertEqual(run_change(project, change_id)["status"], "BASE_REVIEW_REQUIRED")
        finally:
            CONFIRMED.reset(token)

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
