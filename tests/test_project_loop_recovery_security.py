from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from storyloop.changes import create_change
from storyloop.models import StoryLoopError
from storyloop.project_loop import LoopServices, load_run_state, run_change
from storyloop.project_git import finish_feature as finish_feature_real
from storyloop.storage import write_bytes_atomic as write_bytes_atomic_real
from tests.support import product
from tests.test_baseline_behavior import git, repository
from tests.test_project_loop import smoke_payload


NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)


class ProjectLoopRecoverySecurityTests(unittest.TestCase):
    def _approved(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        repository(root)
        bootstrap_project(root, {"test_commands": []})
        state = create_change(root, "orders", product(), now=lambda: NOW)
        approve_change(root, state["change_id"], now=lambda: NOW)
        return temporary, root, state["change_id"]

    def _services(self, change_id, generate=None):
        def implement(project, context):
            path = project / "modules/orders/service.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("VALUE = 1\n", encoding="utf-8")
            return {"summary": "implemented", "questions": []}

        return LoopServices(
            generate_smoke=generate or (lambda project, change: smoke_payload(change_id)),
            implement=implement,
            review=lambda project, context: {
                "verdict": "pass", "summary": "ok", "findings": [], "questions": [],
            },
        )

    def test_preparing_crash_resumes_contract_and_red_generation(self):
        temporary, root, change_id = self._approved()
        try:
            def crash(project, change):
                raise StoryLoopError("generator crashed")

            with self.assertRaisesRegex(StoryLoopError, "crashed"):
                run_change(root, change_id, self._services(change_id, crash))
            self.assertEqual(load_run_state(root, change_id)["status"], "PREPARING")
            result = run_change(root, change_id, self._services(change_id))
            self.assertEqual(result["status"], "PASSED")
        finally:
            temporary.cleanup()

    def test_verified_resume_rejects_an_extra_unverified_commit(self):
        temporary, root, change_id = self._approved()
        try:
            with patch("storyloop.project_loop.finish_feature",
                       side_effect=StoryLoopError("simulated merge crash")):
                with self.assertRaisesRegex(StoryLoopError, "simulated"):
                    run_change(root, change_id, self._services(change_id))
            self.assertEqual(load_run_state(root, change_id)["status"], "VERIFIED")
            rogue = root / "modules/rogue.py"
            rogue.write_text("UNVERIFIED = True\n", encoding="utf-8")
            git(root, "add", "modules/rogue.py")
            git(root, "commit", "-q", "-m", "unverified")
            with self.assertRaisesRegex(StoryLoopError, "changed after reporting"):
                run_change(root, change_id, self._services(change_id))
        finally:
            temporary.cleanup()

    def test_verified_resume_rejects_a_merge_with_a_different_tree(self):
        temporary, root, change_id = self._approved()
        try:
            def tampered_merge(project, feature, source):
                finish_feature_real(project, feature, source)
                target = project / "modules/orders/service.py"
                target.write_text("VALUE = 999\n", encoding="utf-8")
                git(project, "add", "modules/orders/service.py")
                git(project, "commit", "--amend", "--no-edit", "--no-verify")
                raise StoryLoopError("simulated crash")

            with patch("storyloop.project_loop.finish_feature", side_effect=tampered_merge):
                with self.assertRaisesRegex(StoryLoopError, "simulated crash"):
                    run_change(root, change_id, self._services(change_id))
            with self.assertRaisesRegex(StoryLoopError, "tree differs"):
                run_change(root, change_id, self._services(change_id))
        finally:
            temporary.cleanup()

    def test_partial_merge_receipt_write_is_regenerated_on_resume(self):
        temporary, root, change_id = self._approved()
        try:
            writes = 0

            def crash_after_json(path, content):
                nonlocal writes
                writes += 1
                if writes == 2:
                    raise OSError("simulated receipt crash")
                write_bytes_atomic_real(path, content)

            with patch("storyloop.change_report.write_bytes_atomic",
                       side_effect=crash_after_json):
                with self.assertRaisesRegex(OSError, "receipt crash"):
                    run_change(root, change_id, self._services(change_id))
            self.assertEqual(load_run_state(root, change_id)["status"], "RECEIPTING")

            result = run_change(root, change_id, self._services(change_id))
            self.assertEqual(result["status"], "PASSED")
        finally:
            temporary.cleanup()

    def test_forged_report_only_child_of_merge_is_rejected(self):
        temporary, root, change_id = self._approved()
        try:
            with patch("storyloop.project_loop._finish_receipting",
                       side_effect=StoryLoopError("simulated receipt pause")):
                with self.assertRaisesRegex(StoryLoopError, "receipt pause"):
                    run_change(root, change_id, self._services(change_id))
            state = load_run_state(root, change_id)
            report_path = Path(state["report"])
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["requirement"]["goal"] = "forged"
            report["merge_commit"] = state["merge_commit"]
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
            report_path.with_suffix(".md").write_text("# forged\n", encoding="utf-8")
            report_root = f"reports/storyloop/{change_id}"
            git(root, "add", f"{report_root}/report.json", f"{report_root}/report.md")
            git(root, "commit", "-q", "-m", "forged report receipt")

            with self.assertRaisesRegex(StoryLoopError, "recover the merge receipt"):
                run_change(root, change_id, self._services(change_id))
        finally:
            temporary.cleanup()

    def test_fixed_ignored_build_artifact_cannot_make_smoke_green(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        repository(root)
        (root / ".gitignore").write_text("target/\n", encoding="utf-8")
        git(root, "add", ".gitignore")
        git(root, "commit", "-q", "-m", "ignore build")
        bootstrap_project(root, {"test_commands": []})
        state = create_change(root, "orders", product(), now=lambda: NOW)
        approve_change(root, state["change_id"], now=lambda: NOW)
        change_id = state["change_id"]
        try:
            source = (
                "from pathlib import Path\n"
                "raise SystemExit(0 if Path('target/runtime.flag').is_file() else 1)\n"
            )
            payload = {"files": [{"path": "flow.py", "content": source}],
                       "manifest": {"version": 1, "tests": [{
                           "id": "SMOKE-1", "criteria": ["AC-1"],
                           "command": ["{python}",
                                       f"acceptance/smoke/{change_id}/flow.py"],
                           "timeout_seconds": 10}]}}

            def implement(project, context):
                code = project / "modules/orders/service.py"
                code.parent.mkdir(parents=True, exist_ok=True)
                code.write_text("VALUE = 1\n", encoding="utf-8")
                marker = project / "target/runtime.flag"
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("local-only\n", encoding="utf-8")
                return {"summary": "local artifact", "questions": []}

            with self.assertRaises(StoryLoopError) as raised:
                run_change(root, change_id, LoopServices(
                    generate_smoke=lambda project, change: payload,
                    implement=implement,
                    review=lambda project, context: {
                        "verdict": "pass", "summary": "ok", "findings": [], "questions": [],
                    },
                ))
            self.assertEqual(raised.exception.code, "WORKSPACE_TAINTED")
            self.assertEqual(load_run_state(root, change_id)["status"], "WORKSPACE_TAINTED")
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
