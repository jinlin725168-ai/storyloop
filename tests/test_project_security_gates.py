from datetime import datetime, timezone
from pathlib import Path
import json
import sys
import tempfile
import unittest

from storyloop.approvals import approve_change, materialize_approved_change
from storyloop.baseline import bootstrap_project, verify_baseline
from storyloop.changes import create_change, draft_directory
from storyloop.models import StoryLoopError
from storyloop.project_loop import LoopServices, load_run_state, run_change
from storyloop.smoke import install_smoke_suite
from storyloop.smoke_runner import run_smoke
from tests.support import product
from tests.test_baseline_behavior import git, repository
from tests.test_project_loop import smoke_payload


NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)


class ProjectSecurityGateTests(unittest.TestCase):
    def _approved(self, root: Path, overrides=None) -> str:
        bootstrap_project(root, overrides or {"test_commands": []})
        state = create_change(root, "orders", product(), now=lambda: NOW)
        approve_change(root, state["change_id"], now=lambda: NOW)
        return state["change_id"]

    def test_committed_foundation_drift_is_rejected_before_branch_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / "core.py").write_text("LOCKED = True\n", encoding="utf-8")
            git(root, "add", "core.py")
            git(root, "commit", "-q", "-m", "core")
            bootstrap_project(root)
            (root / "core.py").write_text("LOCKED = False\n", encoding="utf-8")
            git(root, "add", "core.py")
            git(root, "commit", "-q", "-m", "drift")
            with self.assertRaisesRegex(StoryLoopError, "Foundation drift"):
                verify_baseline(root)

    def test_materialization_rebuilds_tampered_draft_from_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            change_id = self._approved(root)
            draft = draft_directory(root, change_id) / "stories.json"
            draft.write_text('{"tampered":true}', encoding="utf-8")
            destination = materialize_approved_change(root, change_id)
            value = json.loads((destination / "stories.json").read_text(encoding="utf-8"))
            self.assertEqual(value["stories"][0]["id"], "ST-1")

    def test_git_ignored_runtime_dependency_is_tainted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / ".gitignore").write_text("runtime.flag\n", encoding="utf-8")
            git(root, "add", ".gitignore")
            git(root, "commit", "-q", "-m", "ignore")
            change_id = self._approved(root)

            def implement(project, context):
                target = project / "modules/orders/service.py"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("VALUE = 1\n", encoding="utf-8")
                (project / "runtime.flag").write_text("local-only\n", encoding="utf-8")
                return {"summary": "used ignored state", "questions": []}

            with self.assertRaisesRegex(StoryLoopError, "ignored"):
                run_change(root, change_id, LoopServices(
                    generate_smoke=lambda project, change: smoke_payload(change_id),
                    implement=implement,
                    review=lambda project, context: {
                        "verdict": "pass", "summary": "ok", "findings": [], "questions": [],
                    },
                ))
            self.assertEqual(load_run_state(root, change_id)["status"], "WORKSPACE_TAINTED")

    def test_red_must_cover_every_automated_criterion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            value = product()
            value["stories"][0]["acceptance"].append({
                "id": "AC-2", "given": "a room", "when": "listed",
                "then": "show it", "kind": "automated",
            })
            bootstrap_project(root)
            state = create_change(root, "two paths", value, now=lambda: NOW)
            approve_change(root, state["change_id"], now=lambda: NOW)
            change_id = state["change_id"]
            install_smoke_suite(root, change_id, [
                {"path": "fail.py", "content": "raise SystemExit(1)\n"},
                {"path": "pass.py", "content": "raise SystemExit(0)\n"},
            ], {"version": 1, "tests": [
                {"id": "S1", "criteria": ["AC-1"],
                 "command": ["{python}", f"acceptance/changes/{change_id}/fail.py"],
                 "timeout_seconds": 10},
                {"id": "S2", "criteria": ["AC-2"],
                 "command": ["{python}", f"acceptance/changes/{change_id}/pass.py"],
                 "timeout_seconds": 10},
            ]})
            with self.assertRaisesRegex(StoryLoopError, "every automated criterion"):
                run_smoke(root, change_id, expect_red=True)

    def test_regression_command_cannot_commit_ci_changes_from_isolated_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            ci_before = (root / ".github/workflows/ci.yml").read_bytes()
            script = (
                "from pathlib import Path; import subprocess; "
                "Path('.github/workflows/ci.yml').write_text('name: tampered\\n'); "
                "subprocess.run(['git','add','.github/workflows/ci.yml'],check=True); "
                "subprocess.run(['git','commit','-m','tamper'],check=True)"
            )
            change_id = self._approved(
                root, {"test_commands": [[sys.executable, "-c", script]]}
            )

            def implement(project, context):
                target = project / "modules/orders/service.py"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("VALUE = 1\n", encoding="utf-8")
                return {"summary": "implemented", "questions": []}

            result = run_change(root, change_id, LoopServices(
                generate_smoke=lambda project, change: smoke_payload(change_id),
                implement=implement,
                review=lambda project, context: {
                    "verdict": "pass", "summary": "ok", "findings": [], "questions": [],
                },
            ))
            self.assertEqual(result["status"], "BUDGET_EXHAUSTED")
            self.assertEqual((root / ".github/workflows/ci.yml").read_bytes(), ci_before)
            self.assertNotIn("tamper", git(root, "log", "--format=%s"))


if __name__ == "__main__":
    unittest.main()
