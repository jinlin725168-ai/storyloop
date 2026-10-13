"""A layer that ran nothing, or fewer tests than the old code had, has not proven anything."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project, load_project_config
from storyloop.changes import create_change
from storyloop.oracle_counts import check_layers
from storyloop.project_loop import LoopServices, load_run_state, run_change
from storyloop.storage import write_json
from tests.support import product
from tests.test_baseline_behavior import git, repository
from tests.test_project_loop import smoke_payload

COUNTER = ("from pathlib import Path\n"
           "n = len(list(Path('tests').glob('test_*.py')))\n"
           "print(f'Ran {n} tests in 0.001s'); print(); print('OK')\n")


def review(project, context):
    return {"verdict": "pass", "summary": "ok", "findings": [], "questions": []}


def counting_repo(root: Path, value: dict) -> str:
    """Unit 'runner' reports one test per tests/test_*.py file, so deleting a file drops the count."""
    repository(root)
    (root / "tools").mkdir()
    (root / "tools/count.py").write_text(COUNTER, encoding="utf-8")
    (root / "tests").mkdir()
    for name in ("test_a.py", "test_b.py"):
        (root / "tests" / name).write_text("pass\n", encoding="utf-8")
    git(root, "add", "."); git(root, "commit", "-q", "-m", "tests")
    bootstrap_project(root, {"unit_commands": [["{python}", "tools/count.py"]], "test_roots": ["tests"]})
    config = load_project_config(root); config["max_attempts"] = 1
    write_json(root / ".storyloop/config.json", config)
    change_id = create_change(root, "orders", value)["change_id"]
    approve_change(root, change_id)
    return change_id


def implementer(delete: bool):
    def implement(project, context):
        (project / "modules/orders").mkdir(parents=True, exist_ok=True)
        (project / "modules/orders/service.py").write_text("VALUE = 1\n", encoding="utf-8")
        if delete:
            (project / "tests/test_b.py").unlink(missing_ok=True)
        return {"summary": "done", "questions": []}
    return implement


class LayerCheckTests(unittest.TestCase):
    def test_zero_executed_fails_a_green_layer_and_uncounted_is_reported(self):
        layers = {"unit": {"status": "PASSED", "results": [
            {"id": "R1", "counts": {"executed": 0}}, {"id": "R2", "counts": {"executed": None}}]}}
        checked = check_layers(layers, None, explained=False)
        self.assertEqual(checked["unit"]["status"], "FAILED")
        self.assertIn("R1", checked["unit"]["oracle_problems"][0])
        self.assertEqual(checked["unit"]["uncounted"], ["R2"])

    def test_a_run_from_before_the_upgrade_has_no_baseline_and_still_works(self):
        layers = {"unit": {"status": "PASSED", "results": [{"id": "R1", "counts": {"executed": 3}}]}}
        self.assertEqual(check_layers(layers, {}, explained=False)["unit"]["status"], "PASSED")


class BaselineLoopTests(unittest.TestCase):
    def test_baseline_is_sampled_and_a_deleted_test_fails_the_unit_layer(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = counting_repo(root, product())
            result = run_change(root, change_id, LoopServices(
                generate_smoke=lambda p, s: smoke_payload(change_id), implement=implementer(True), review=review))
            self.assertEqual(result["status"], "BUDGET_EXHAUSTED")
            state = load_run_state(root, change_id)
            self.assertEqual(state["oracle"]["baseline"]["unit"], [2])
            unit = state["failures"][0]["unit"]
            self.assertIn("从 2 降到 1", unit["oracle_problems"][0])

    def test_a_declared_removal_explains_the_drop(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            value = product()
            value["test_removals"] = [{"path": "tests/test_b.py", "reason": "随死代码一起删除"}]
            change_id = counting_repo(root, value)
            result = run_change(root, change_id, LoopServices(
                generate_smoke=lambda p, s: smoke_payload(change_id), implement=implementer(True), review=review))
            self.assertEqual(result["status"], "PASSED", result)


class DeletedFileCandidateTests(unittest.TestCase):
    def test_a_tracked_file_deleted_in_the_workspace_is_absent_from_the_candidate(self):
        from storyloop.smoke_runner import implementation_digest, run_commands_isolated
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / "gone.py").write_text("x = 1\n", encoding="utf-8")
            git(root, "add", "."); git(root, "commit", "-q", "-m", "gone")
            bootstrap_project(root, {"test_commands": []})
            (root / "gone.py").unlink()
            check = [sys.executable, "-c", "import os, sys; sys.exit(1 if os.path.exists('gone.py') else 0)"]
            self.assertEqual(run_commands_isolated(root, [check], 30)["status"], "PASSED")
            self.assertEqual(len(implementation_digest(root)), 64)


if __name__ == "__main__":
    unittest.main()
