"""A defective smoke suite can be repaired by its owner (smoke role) and RED re-proven at the contract commit."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project, load_project_config
from storyloop.changes import create_change
from storyloop.models import StoryLoopError
from storyloop.project_loop import LoopServices, load_run_state, repair_smoke, run_change
from storyloop.smoke_runner import _copy_candidate
from storyloop.storage import write_json
from tests.test_baseline_behavior import git, repository
from tests.test_project_loop import smoke_payload as good_payload
from tests.test_product_spec import rich_product

NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)


def defective_payload(change_id: str) -> dict:
    """Fails whatever the implementation does (a test defect), yet proves RED like a real suite."""
    payload = good_payload(change_id)
    payload["files"][0]["content"] = "raise SystemExit(1)\n"
    return payload


def services(change_id: str, smoke) -> LoopServices:
    def implement(project, context):
        target = project / "modules/orders/service.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("VALUE = 1\n", encoding="utf-8")
        return {"summary": "done", "questions": []}

    return LoopServices(generate_smoke=smoke, implement=implement,
                        review=lambda p, c: {"verdict": "pass", "summary": "ok", "findings": [], "questions": []})


class CandidateTests(unittest.TestCase):
    def test_ui_regression_assets_stay_out_of_acceptance_candidates(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as target:
            root = Path(folder)
            repository(root)
            (root / "acceptance/ui/cap").mkdir(parents=True)
            (root / "acceptance/ui/cap/menu.spec.ts").write_text("// asset\n", encoding="utf-8")
            (root / "acceptance/changes").mkdir(parents=True)
            (root / "acceptance/changes/keep.py").write_text("pass\n", encoding="utf-8")
            git(root, "add", "."); git(root, "commit", "-q", "-m", "assets")
            bootstrap_project(root)
            paths = _copy_candidate(root, Path(target))
            self.assertIn("acceptance/changes/keep.py", paths)
            self.assertFalse(any(path.startswith("acceptance/ui/") for path in paths))
            self.assertFalse((Path(target) / "acceptance/ui").exists())


class SmokeRepairTests(unittest.TestCase):
    def test_repair_replaces_suite_reproves_red_at_contract_commit_and_run_passes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root, {"test_commands": []})
            config = load_project_config(root); config["max_attempts"] = 1
            write_json(root / ".storyloop/config.json", config)
            value = rich_product(); value["questions"] = []
            state = create_change(root, "orders", value, now=lambda: NOW)
            change_id = state["change_id"]
            approve_change(root, change_id, now=lambda: NOW)

            exhausted = run_change(root, change_id, services(change_id, lambda p, s: defective_payload(change_id)))
            self.assertEqual(exhausted["status"], "BUDGET_EXHAUSTED", exhausted)
            old_red = load_run_state(root, change_id)["red_evidence_sha256"]

            seen = {}

            def repaired_smoke(project, change):
                seen["repair"] = change.get("repair")
                payload = good_payload(change_id)
                payload["files"].append({"path": "manifest.json", "content": "{}"})  # echoed back; must be ignored
                return payload

            result = repair_smoke(root, change_id, "loading-mask locator is a test defect",
                                  services(change_id, repaired_smoke), now=lambda: NOW)
            self.assertEqual(result["status"], "SMOKE_REPAIRED", result)
            self.assertEqual(seen["repair"]["reason"], "loading-mask locator is a test defect")
            self.assertTrue(any(f["path"] == "flow.py" for f in seen["repair"]["existing_files"]))
            self.assertFalse(any(f["path"] == "manifest.json" for f in seen["repair"]["existing_files"]))
            self.assertIn("failure_evidence", seen["repair"])
            new_state = load_run_state(root, change_id)
            self.assertEqual(new_state["status"], "IMPLEMENTING")
            self.assertEqual(new_state["attempt"], 0)
            self.assertNotEqual(new_state["red_evidence_sha256"], old_red)
            self.assertEqual(new_state["repairs"][0]["reason"], "loading-mask locator is a test defect")
            log = subprocess.run(["git", "log", "--oneline", "-3"], cwd=root, capture_output=True, text=True).stdout
            self.assertIn("repair smoke suite", log)
            # implementation files from the failed attempt are still in the tree, uncommitted
            self.assertTrue((root / "modules/orders/service.py").is_file())
            red = json.loads((root / ".storyloop/evidence" / change_id / "acceptance-red.json").read_text(encoding="utf-8"))
            self.assertEqual(red["status"], "RED")
            self.assertEqual(red["head_commit"], new_state["contract_commit"])

            done = run_change(root, change_id, services(change_id, lambda p, s: good_payload(change_id)))
            self.assertEqual(done["status"], "PASSED", done)
            decisions = (root / "openspec/changes" / change_id / "decisions.md").read_text(encoding="utf-8")
            self.assertIn("loading-mask locator is a test defect", decisions)

    def test_repair_is_refused_before_a_contract_exists_or_after_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root, {"test_commands": []})
            value = rich_product(); value["questions"] = []
            state = create_change(root, "orders", value, now=lambda: NOW)
            change_id = state["change_id"]
            with self.assertRaises(StoryLoopError):
                repair_smoke(root, change_id, "too early", services(change_id, lambda p, s: good_payload(change_id)))
            approve_change(root, change_id, now=lambda: NOW)
            run_change(root, change_id, services(change_id, lambda p, s: good_payload(change_id)))
            with self.assertRaises(StoryLoopError):
                repair_smoke(root, change_id, "already passed", services(change_id, lambda p, s: good_payload(change_id)))


if __name__ == "__main__":
    unittest.main()
