"""The base G1 gate uses green checks on the locked old foundation."""

from pathlib import Path
import sys
import tempfile
import unittest

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

    def test_human_run_opens_branch_and_stops_without_design_agent(self):
        result = run_change(self.root, self.change["change_id"], implementer="human")
        self.assertEqual(result["status"], "HUMAN_IMPLEMENTATION_REQUIRED")
        self.assertEqual(git(self.root, "branch", "--show-current"),
                         f"storyloop/{self.change['change_id']}")

    def test_human_candidate_rejects_unapproved_committed_path(self):
        run_change(self.root, self.change["change_id"], implementer="human")
        (self.root / "rogue.py").write_text("VALUE = 1\n", encoding="utf-8")
        git(self.root, "add", "rogue.py")
        git(self.root, "commit", "-q", "-m", "rogue")
        with self.assertRaisesRegex(StoryLoopError, "rogue.py"):
            run_change(self.root, self.change["change_id"])

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
        run_change(self.root, approved["change_id"], implementer="human")
        (self.root / "docs").mkdir()
        (self.root / "docs/base.md").write_text("base\n", encoding="utf-8")
        git(self.root, "add", "docs/base.md")
        git(self.root, "commit", "-q", "-m", "base")
        with self.assertRaisesRegex(StoryLoopError, "scope"):
            run_change(self.root, approved["change_id"])

    def test_human_candidate_requires_independent_review_and_fixed_acceptance(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, implementer="human")
        (self.root / "pom.xml").write_text("<project><version>2</version></project>\n", encoding="utf-8")
        git(self.root, "add", "pom.xml")
        git(self.root, "commit", "-q", "-m", "base: update pom")
        self.assertEqual(run_change(self.root, change_id)["status"], "IMPLEMENTING")
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


if __name__ == "__main__":
    unittest.main()
