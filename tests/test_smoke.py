from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from storyloop.changes import load_change
from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from tests.routing_support import legacy_create_change as create_change
from storyloop.models import StoryLoopError
from storyloop.smoke import install_smoke_suite, load_smoke_manifest
from storyloop.smoke_runner import run_smoke_isolated
from tests.support import product
from tests.test_baseline_behavior import repository


NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)


class SmokeSuiteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        repository(self.root)
        bootstrap_project(self.root)
        state = create_change(self.root, "idea", product(), now=lambda: NOW)
        self.change_id = state["change_id"]
        approve_change(self.root, self.change_id, now=lambda: NOW)

    def tearDown(self):
        self.temporary.cleanup()

    def test_installs_criterion_linked_tests_and_records_hashes(self):
        manifest = {
            "version": 1,
            "tests": [{
                "id": "SMOKE-1",
                "criteria": ["AC-1"],
                "command": ["{python}", f"acceptance/changes/{self.change_id}/test_smoke.py"],
                "timeout_seconds": 10,
            }],
        }
        result = install_smoke_suite(
            self.root,
            load_change(self.root, self.change_id),
            [{"path": "test_smoke.py", "content": "raise SystemExit(1)\n"}],
            manifest,
        )

        loaded = load_smoke_manifest(self.root, load_change(self.root, self.change_id))
        self.assertEqual(loaded, manifest)
        self.assertEqual(result["status"], "SMOKE_INSTALLED")
        self.assertIn("test_smoke.py", result["digests"])
        self.assertEqual(len(result["digests"]["test_smoke.py"]), 64)

    def test_rejects_ci_output_traversal_shell_commands_and_missing_coverage(self):
        base = {"version": 1, "tests": [{
            "id": "SMOKE-1", "criteria": ["AC-1"],
            "command": "python test.py", "timeout_seconds": 10,
        }]}
        with self.assertRaisesRegex(StoryLoopError, "argv"):
            install_smoke_suite(self.root, load_change(self.root, self.change_id), [], base)

        no_coverage = {"version": 1, "tests": [{
            "id": "SMOKE-1", "criteria": [], "command": ["python", "test.py"],
            "timeout_seconds": 10,
        }]}
        with self.assertRaisesRegex(StoryLoopError, "uncovered"):
            install_smoke_suite(self.root, load_change(self.root, self.change_id), [], no_coverage)

        valid = {"version": 1, "tests": [{
            "id": "SMOKE-1", "criteria": ["AC-1"], "command": ["python", "test.py"],
            "timeout_seconds": 10,
        }]}
        with self.assertRaisesRegex(StoryLoopError, "CI|path"):
            install_smoke_suite(
                self.root, load_change(self.root, self.change_id),
                [{"path": "../../.github/workflows/ci.yml", "content": "bad"}], valid,
            )

    def test_manifest_is_checked_against_the_supplied_approved_contract(self):
        change = load_change(self.root, self.change_id)
        manifest = {"version": 1, "tests": [{
            "id": "SMOKE-1", "criteria": ["AC-1"],
            "command": ["{python}", "-V"], "timeout_seconds": 10,
        }]}
        install_smoke_suite(self.root, change, [{"path": "smoke.py", "content": "pass\n"}], manifest)
        changed_contract = deepcopy(change)
        changed_contract["product"]["stories"][0]["acceptance"][0]["id"] = "AC-OTHER"
        with self.assertRaisesRegex(StoryLoopError, "unknown criterion"):
            load_smoke_manifest(self.root, changed_contract)

    def test_isolated_smoke_cannot_modify_the_candidate_to_make_itself_pass(self):
        script = (
            "from pathlib import Path\n"
            "p = Path('modules/poison.py')\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            "p.write_text('POISON = True\\n', encoding='utf-8')\n"
        )
        manifest = {"version": 1, "tests": [{
            "id": "SMOKE-1", "criteria": ["AC-1"],
            "command": ["{python}",
                        f"acceptance/changes/{self.change_id}/test_smoke.py"],
            "timeout_seconds": 10,
        }]}
        install_smoke_suite(
            self.root, load_change(self.root, self.change_id),
            [{"path": "test_smoke.py", "content": script}], manifest,
        )

        with self.assertRaisesRegex(StoryLoopError, "modified its isolated candidate"):
            run_smoke_isolated(self.root, load_change(self.root, self.change_id))


if __name__ == "__main__":
    unittest.main()
