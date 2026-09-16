from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from storyloop.base_upgrade import upgrade_baseline
from storyloop.baseline import bootstrap_project, load_baseline
from storyloop.project_boundaries import audit_project_changes
from storyloop.storage import load_json, write_json
from storyloop.models import StoryLoopError
from tests.test_baseline_behavior import git, repository


class BaseUpgradeTests(unittest.TestCase):
    def test_exact_proposed_base_commit_passes_regression_and_bumps_version(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / "core.py").write_text("STABLE = True\n", encoding="utf-8")
            module = root / "modules/orders/service.py"
            module.parent.mkdir(parents=True)
            module.write_text("VALUE = 1\n", encoding="utf-8")
            git(root, "add", "core.py")
            git(root, "add", "modules")
            git(root, "commit", "-q", "-m", "core")
            bootstrap_project(root, {"test_commands": [[sys.executable, "-c", "pass"]]})

            (root / "core.py").write_text("STABLE = False\n", encoding="utf-8")
            audit = audit_project_changes(root, "FEAT-20260916-001")
            proposal_path = Path(audit["base_proposal"])
            proposal_id = proposal_path.stem
            (root / "core.py").write_text("STABLE = True\n", encoding="utf-8")

            (root / "core.py").write_text("STABLE = False\n", encoding="utf-8")
            git(root, "add", "core.py")
            git(root, "commit", "-q", "-m", "base: update core")
            result = upgrade_baseline(root, proposal_id)

            self.assertEqual(result["status"], "BASELINE_UPGRADED")
            self.assertEqual(load_baseline(root)["baseline_version"], "1.0.1")
            self.assertEqual(load_json(proposal_path)["status"], "ACCEPTED")
            module.write_text("VALUE = 2\n", encoding="utf-8")
            audit_after = audit_project_changes(root, "FEAT-20260916-002")
            self.assertEqual(audit_after["allowed"][0]["category"], "FEATURE")

    def test_base_candidate_cannot_carry_an_unapproved_module_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / "core.py").write_text("STABLE = True\n", encoding="utf-8")
            git(root, "add", "core.py")
            git(root, "commit", "-q", "-m", "core")
            bootstrap_project(root, {"test_commands": [[sys.executable, "-c", "pass"]]})
            (root / "core.py").write_text("STABLE = False\n", encoding="utf-8")
            proposal = Path(audit_project_changes(
                root, "FEAT-20260916-001"
            )["base_proposal"])
            module = root / "modules/rogue.py"
            module.parent.mkdir(parents=True)
            module.write_text("ROGUE = True\n", encoding="utf-8")
            git(root, "add", "core.py", "modules/rogue.py")
            git(root, "commit", "-q", "-m", "mixed base and feature")
            with self.assertRaisesRegex(StoryLoopError, "exactly match"):
                upgrade_baseline(root, proposal.stem)

    def test_accepting_recovery_revalidates_the_committed_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / "core.py").write_text("STABLE = True\n", encoding="utf-8")
            git(root, "add", "core.py")
            git(root, "commit", "-q", "-m", "core")
            bootstrap_project(root, {"test_commands": [[sys.executable, "-c", "pass"]]})
            (root / "core.py").write_text("STABLE = False\n", encoding="utf-8")
            proposal = Path(audit_project_changes(
                root, "FEAT-20260916-001"
            )["base_proposal"])
            git(root, "add", "core.py")
            git(root, "commit", "-q", "-m", "approved base candidate")
            with patch("storyloop.base_upgrade._recover_upgrade",
                       side_effect=StoryLoopError("simulated crash")):
                with self.assertRaisesRegex(StoryLoopError, "simulated crash"):
                    upgrade_baseline(root, proposal.stem)
            rogue = root / "modules/rogue.py"
            rogue.parent.mkdir(parents=True)
            rogue.write_text("ROGUE = True\n", encoding="utf-8")
            git(root, "add", "modules/rogue.py")
            git(root, "commit", "-q", "-m", "replace candidate")

            with self.assertRaisesRegex(StoryLoopError, "[Cc]andidate.*changed"):
                upgrade_baseline(root, proposal.stem)

    def test_accepting_recovery_rejects_a_changed_proposal_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / "core.py").write_text("STABLE = True\n", encoding="utf-8")
            git(root, "add", "core.py")
            git(root, "commit", "-q", "-m", "core")
            bootstrap_project(root, {"test_commands": [[sys.executable, "-c", "pass"]]})
            (root / "core.py").write_text("STABLE = False\n", encoding="utf-8")
            proposal = Path(audit_project_changes(
                root, "FEAT-20260916-001"
            )["base_proposal"])
            git(root, "add", "core.py")
            git(root, "commit", "-q", "-m", "approved base candidate")
            with patch("storyloop.base_upgrade._recover_upgrade",
                       side_effect=StoryLoopError("simulated crash")):
                with self.assertRaisesRegex(StoryLoopError, "simulated crash"):
                    upgrade_baseline(root, proposal.stem)
            tampered = load_json(proposal)
            tampered["changes"] = [{"path": "modules/rogue.py", "category": "FEATURE"}]
            write_json(proposal, tampered)

            with self.assertRaisesRegex(StoryLoopError, "changed after acceptance"):
                upgrade_baseline(root, proposal.stem)

    def test_base_regression_build_outputs_are_discarded_with_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / "core.py").write_text("STABLE = True\n", encoding="utf-8")
            git(root, "add", "core.py")
            git(root, "commit", "-q", "-m", "core")
            command = [sys.executable, "-c",
                       "from pathlib import Path; Path('target').mkdir(); "
                       "Path('target/cache.bin').write_bytes(b'x')"]
            bootstrap_project(root, {"test_commands": [command]})
            (root / "core.py").write_text("STABLE = False\n", encoding="utf-8")
            proposal = Path(audit_project_changes(
                root, "FEAT-20260916-001"
            )["base_proposal"])
            git(root, "add", "core.py")
            git(root, "commit", "-q", "-m", "base candidate")

            result = upgrade_baseline(root, proposal.stem)
            self.assertEqual(result["status"], "BASELINE_UPGRADED")
            self.assertFalse((root / "target").exists())

    def test_accepting_recovery_rejects_changed_project_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / "core.py").write_text("STABLE = True\n", encoding="utf-8")
            git(root, "add", "core.py")
            git(root, "commit", "-q", "-m", "core")
            bootstrap_project(root, {"test_commands": [[sys.executable, "-c", "pass"]]})
            (root / "core.py").write_text("STABLE = False\n", encoding="utf-8")
            proposal = Path(audit_project_changes(
                root, "FEAT-20260916-001"
            )["base_proposal"])
            git(root, "add", "core.py")
            git(root, "commit", "-q", "-m", "base candidate")
            with patch("storyloop.base_upgrade._recover_upgrade",
                       side_effect=StoryLoopError("simulated crash")):
                with self.assertRaisesRegex(StoryLoopError, "simulated crash"):
                    upgrade_baseline(root, proposal.stem)
            config_path = root / ".storyloop/config.json"
            config = load_json(config_path)
            config["profile"]["module_roots"] = ["everything"]
            write_json(config_path, config)

            with self.assertRaisesRegex(StoryLoopError, "configuration changed"):
                upgrade_baseline(root, proposal.stem)

    def test_accepted_upgrade_rejects_tampered_baseline_on_idempotent_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / "core.py").write_text("STABLE = True\n", encoding="utf-8")
            git(root, "add", "core.py")
            git(root, "commit", "-q", "-m", "core")
            bootstrap_project(root, {"test_commands": [[sys.executable, "-c", "pass"]]})
            (root / "core.py").write_text("STABLE = False\n", encoding="utf-8")
            proposal = Path(audit_project_changes(
                root, "FEAT-20260916-001"
            )["base_proposal"])
            git(root, "add", "core.py")
            git(root, "commit", "-q", "-m", "base candidate")
            upgrade_baseline(root, proposal.stem)
            baseline_path = root / ".storyloop/baseline.json"
            baseline = load_json(baseline_path)
            baseline["module_roots"] = ["everything"]
            write_json(baseline_path, baseline)

            with self.assertRaisesRegex(StoryLoopError, "accepted upgrade transition"):
                upgrade_baseline(root, proposal.stem)


if __name__ == "__main__":
    unittest.main()
