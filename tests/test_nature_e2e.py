"""A fix and a refactor run the whole loop with scripted agents: each nature gets its own oracle."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from tests.routing_support import legacy_create_change as create_change
from storyloop.project_loop import LoopServices, load_run_state, run_change
from tests.test_baseline_behavior import git
from tests.test_change_nature import fix_product, nature_repository, refactor_product
from tests.test_project_loop import smoke_payload

NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)
UNIT = ["{python}", "-m", "unittest", "discover", "-s", "tests", "-t", "."]
LOCK = ("import unittest\nfrom modules.orders import service\n\n\nclass Lock(unittest.TestCase):\n"
        "    def test_value(self):\n        self.assertEqual(service.VALUE, 1)\n")
STRUCTURE = ("from pathlib import Path\nimport sys\n"
             "text = Path('modules/orders/service.py').read_text(encoding='utf-8')\n"
             "print('Ran 1 tests in 0.0s'); sys.exit(1 if 'legacy' in text else 0)\n")


def review(project, context):
    return {"verdict": "pass", "summary": "ok", "findings": [], "questions": []}


class FixEndToEndTests(unittest.TestCase):
    def test_a_fix_reproduces_red_then_passes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            nature_repository(root)
            bootstrap_project(root, {"test_commands": []})
            change_id = create_change(root, "导出少一行", fix_product(), now=lambda: NOW)["change_id"]
            self.assertTrue(change_id.startswith("FIX-"))
            approve_change(root, change_id, now=lambda: NOW)

            def implement(project, context):
                (project / "modules/orders/service.py").write_text("VALUE = 1\n", encoding="utf-8")
                return {"summary": "fixed", "questions": []}

            result = run_change(root, change_id, LoopServices(
                generate_smoke=lambda p, s: smoke_payload(change_id), implement=implement, review=review))
            self.assertEqual(result["status"], "PASSED", result)


class RefactorEndToEndTests(unittest.TestCase):
    def test_a_refactor_turns_the_structure_green_and_keeps_behaviour(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            nature_repository(root)
            (root / "modules/__init__.py").write_text("", encoding="utf-8")
            (root / "modules/orders/__init__.py").write_text("", encoding="utf-8")
            (root / "modules/orders/service.py").write_text("import json as legacy\nVALUE = 1\n",
                                                             encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests/__init__.py").write_text("", encoding="utf-8")
            (root / "tests/test_existing.py").write_text(
                "import unittest\n\n\nclass E(unittest.TestCase):\n    def test_ok(self):\n        pass\n",
                encoding="utf-8")
            git(root, "add", "."); git(root, "commit", "-q", "-m", "legacy")
            bootstrap_project(root, {"unit_commands": [UNIT], "test_roots": ["tests"]})
            change_id = create_change(root, "去掉 legacy", refactor_product(), now=lambda: NOW)["change_id"]
            self.assertTrue(change_id.startswith("REF-"))
            approve_change(root, change_id, now=lambda: NOW)
            suite = {"files": [{"path": "structure.py", "content": STRUCTURE}],
                     "manifest": {"version": 1, "tests": [{"id": "NO-LEGACY", "criteria": ["AC-1"],
                                  "command": ["{python}", f"acceptance/changes/{change_id}/structure.py"],
                                  "timeout_seconds": 10}]}}

            def characterize(project, context):
                (project / "tests/test_lock_service.py").write_text(LOCK, encoding="utf-8")
                return {"summary": "locked", "touched": ["modules/orders/service.py"],
                        "tests": ["tests/test_lock_service.py"], "questions": []}

            def implement(project, context):
                from storyloop.storage import load_json
                from storyloop.smoke import RED_EVIDENCE
                red = load_json(project / '.storyloop/evidence' / change_id / RED_EVIDENCE)
                self.assertEqual(red['status'], 'RED')
                self.assertTrue(red['suite_digests'])
                (project / "modules/orders/service.py").write_text("VALUE = 1\n", encoding="utf-8")
                return {"summary": "removed legacy", "questions": []}

            result = run_change(root, change_id, LoopServices(
                generate_smoke=lambda p, s: suite, implement=implement, review=review, characterize=characterize))
            self.assertEqual(result["status"], "PASSED", result)
            state = load_run_state(root, change_id)
            self.assertEqual(state["characterization"]["status"], "GREEN")
            self.assertGreaterEqual(state["oracle"]["baseline"]["unit"][0], 2)


if __name__ == "__main__":
    unittest.main()
