"""An old initialized project can confirm build commands without rewriting config.json."""

from pathlib import Path
import sys
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.base_execution import record_base_review
from storyloop.base_upgrade import propose_base_change
from storyloop.baseline import load_baseline
from storyloop.cli import dispatch, parser
from storyloop.manual_acceptance import record_manual_acceptance
from storyloop.models import StoryLoopError
from storyloop.onboarding_cli import bootstrap_with_project_checks
from storyloop.project_loop import run_change
from tests.test_baseline_behavior import git, repository


class BaseSupplementTests(unittest.TestCase):
    def test_old_project_uses_confirmed_supplement_through_complete_human_base_run(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            repository(project)
            smoke = project / "acceptance/smoke"
            smoke.mkdir(parents=True)
            (smoke / "check.py").write_text("print('green')\n", encoding="utf-8")
            (smoke / "manifest.json").write_text(
                '{"version":1,"tests":[{"id":"check","description":"check",'
                '"command":["{python}","acceptance/smoke/check.py"],"timeout_seconds":30}]}',
                encoding="utf-8")
            git(project, "add", ".")
            git(project, "commit", "-q", "-m", "smoke")
            bootstrap_with_project_checks(project, {
                "unit_commands": [[sys.executable, "-c", "print('green')"]],
                "integration_commands": [], "check_commands": [],
            })
            config = project / ".storyloop/config.json"
            original = config.read_bytes()
            change_id = propose_base_change(project, ["pom.xml"], "旧项目更新底座")["change_id"]
            result = dispatch(parser().parse_args([
                "base-build-config", str(project), "--commands-json", "[]",
                "--confirmed-by", "project owner",
            ]))
            self.assertEqual(result["status"], "CONFIRMED")
            approval = approve_change(project, change_id)["approval"]
            self.assertEqual(len(approval["base_build_config_sha256"]), 64)
            self.assertEqual(len(approval["base_commands_sha256"]), 64)
            self.assertEqual(config.read_bytes(), original)
            supplement = project / ".storyloop/base-build-commands.json"
            frozen = supplement.read_bytes()
            with self.assertRaisesRegex(StoryLoopError, "approved base change"):
                dispatch(parser().parse_args([
                    "base-build-config", str(project), "--commands-json", "[]",
                    "--confirmed-by", "another operator",
                ]))
            self.assertEqual(supplement.read_bytes(), frozen)
            self.assertEqual(run_change(project, change_id, implementer="human")["status"],
                             "HUMAN_IMPLEMENTATION_REQUIRED")
            (project / "pom.xml").write_text("<project/>\n<!-- upgraded -->\n", encoding="utf-8")
            git(project, "add", "pom.xml")
            git(project, "commit", "-q", "-m", "human base")
            self.assertEqual(run_change(project, change_id)["status"], "BASE_REVIEW_REQUIRED")
            git(project, "config", "user.name", "Independent Reviewer")
            record_base_review(project, change_id, "pass", "已复核")
            self.assertEqual(run_change(project, change_id)["status"],
                             "MANUAL_ACCEPTANCE_REQUIRED")
            record_manual_acceptance(project, change_id, ["BASE-ACCEPT"], "符合目标")
            self.assertEqual(run_change(project, change_id)["status"], "FINALIZING")
            self.assertEqual(run_change(project, change_id)["status"], "PASSED")
            self.assertEqual(load_baseline(project)["baseline_version"], "1.0.1")
            self.assertEqual(config.read_bytes(), original)

