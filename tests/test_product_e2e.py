import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from storyloop.changes import create_change
from storyloop.project_loop import LoopServices, run_change
from tests.support import product
from tests.test_baseline_behavior import git, repository


class ProductIterationEndToEndTests(unittest.TestCase):
    def test_clean_repo_to_traceable_local_merge_without_ci_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            ci_before = (root / ".github/workflows/ci.yml").read_bytes()
            source_branch = git(root, "branch", "--show-current")
            bootstrap_project(root, {"test_commands": []})
            state = create_change(
                root, "add order module", product(),
                now=lambda: datetime(2026, 9, 16, tzinfo=timezone.utc),
            )
            change_id = state["change_id"]
            approve_change(root, change_id)

            smoke_source = (
                "from pathlib import Path\n"
                "p = Path('modules/orders/service.py')\n"
                "raise SystemExit(0 if p.is_file() and p.read_text().strip() == 'VALUE = 1' else 1)\n"
            )
            smoke = {
                "files": [{"path": "core_flow.py", "content": smoke_source}],
                "manifest": {"version": 1, "tests": [{
                    "id": "SMOKE-ORDER", "criteria": ["AC-1"],
                    "command": ["{python}", f"acceptance/smoke/{change_id}/core_flow.py"],
                    "timeout_seconds": 10,
                }]},
            }

            def implement(project, context):
                path = project / "modules/orders/service.py"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("VALUE = 1\n", encoding="utf-8")
                return {"summary": "implemented", "questions": []}

            result = run_change(root, change_id, LoopServices(
                generate_smoke=lambda project, change: smoke,
                implement=implement,
                review=lambda project, context: {
                    "verdict": "pass", "summary": "independent review passed",
                    "findings": [], "questions": [],
                },
            ))

            report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(git(root, "branch", "--show-current"), source_branch)
            self.assertEqual(git(root, "status", "--porcelain=v1", "--untracked-files=all"), "")
            self.assertEqual((root / ".github/workflows/ci.yml").read_bytes(), ci_before)
            self.assertEqual(report["implementation_commit"], result["implementation_commit"])
            self.assertEqual(report["merge_commit"], result["merge_commit"])
            self.assertEqual(git(root, "rev-parse", "HEAD"), result["integration_commit"])
            self.assertEqual(report["traceability"][0]["tests"][0]["result"]["status"], "PASS")
            tracked = set(git(root, "ls-files").splitlines())
            self.assertIn(f"openspec/changes/{change_id}/approval.json", tracked)
            self.assertIn(f"acceptance/smoke/{change_id}/manifest.json", tracked)
            self.assertIn(f"reports/storyloop/{change_id}/report.json", tracked)


if __name__ == "__main__":
    unittest.main()
