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


if __name__ == "__main__":
    unittest.main()
