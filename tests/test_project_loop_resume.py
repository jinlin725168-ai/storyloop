from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from storyloop.baseline import load_baseline, load_project_config
from storyloop.base_upgrade import _dict_digest, _next_baseline
from storyloop.base_build_config import confirm_legacy_base_build
from tests.routing_support import legacy_create_change as create_change
from storyloop.models import StoryLoopError
from storyloop.project_loop import LoopServices, run_change, load_run_state
from storyloop.project_loop import _follow_base_upgrade
from storyloop.changes import contract_digest
from storyloop.storage import load_json, write_json
from tests.support import product
from tests.test_baseline_behavior import git, repository
from tests.test_project_loop import smoke_payload


class ProjectLoopResumeTests(unittest.TestCase):
    def test_restored_base_violation_resumes_existing_feature_branch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / "core.py").write_text("STABLE = True\n", encoding="utf-8")
            git(root, "add", "core.py")
            git(root, "commit", "-q", "-m", "core")
            bootstrap_project(root, {"test_commands": []})
            state = create_change(
                root, "orders", product(),
                now=lambda: datetime(2026, 9, 16, tzinfo=timezone.utc),
            )
            change_id = state["change_id"]
            approved = approve_change(root, change_id)
            self.assertEqual(approved["approval"]["contract_digest"], contract_digest(product()))

            def base_change(project, context):
                (project / "core.py").write_text("STABLE = False\n", encoding="utf-8")
                return {"summary": "base needed", "questions": []}

            first = run_change(root, change_id, LoopServices(
                generate_smoke=lambda project, change: smoke_payload(change_id),
                implement=base_change,
                review=lambda project, context: {
                    "verdict": "pass", "summary": "ok", "findings": [], "questions": [],
                },
            ))
            self.assertEqual(first["status"], "BASE_CHANGE_REQUIRED")
            self.assertNotIn("routing", load_run_state(root, change_id))
            # A historical stop has no E3 association; its manual restore route remains valid.
            change_path = root / ".storyloop/state/changes" / f"{change_id}.json"
            old_change = load_json(change_path)
            old_change.pop("blocked_by")
            write_json(change_path, old_change)
            run_path = root / ".storyloop/state/runs" / f"{change_id}.json"
            old_run = load_json(run_path)
            old_run.pop("blocked_by")
            write_json(run_path, old_run)
            (root / "core.py").write_text("STABLE = True\n", encoding="utf-8")

            def feature_change(project, context):
                path = project / "modules/orders/service.py"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("VALUE = 1\n", encoding="utf-8")
                return {"summary": "implemented", "questions": []}

            with patch("storyloop.project_loop.require_intake",
                       side_effect=AssertionError("legacy must not run intake")):
                second = run_change(root, change_id, LoopServices(
                    generate_smoke=lambda project, change: self.fail("smoke must not regenerate"),
                    implement=feature_change,
                    review=lambda project, context: {
                        "verdict": "pass", "summary": "ok", "findings": [], "questions": [],
                    },
                ))

            self.assertEqual(second["status"], "PASSED")
            self.assertEqual(second["attempts"], 2)

    def test_finalization_resumes_after_change_status_write_crash(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root, {"test_commands": []})
            state = create_change(
                root, "orders", product(),
                now=lambda: datetime(2026, 9, 16, tzinfo=timezone.utc),
            )
            change_id = state["change_id"]
            approved = approve_change(root, change_id)
            self.assertEqual(approved["approval"]["contract_digest"], contract_digest(product()))

            def implement(project, context):
                path = project / "modules/orders/service.py"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("VALUE = 1\n", encoding="utf-8")
                return {"summary": "implemented", "questions": []}

            services = LoopServices(
                generate_smoke=lambda project, change: smoke_payload(change_id),
                implement=implement,
                review=lambda project, context: {
                    "verdict": "pass", "summary": "ok", "findings": [], "questions": [],
                },
            )
            with patch("storyloop.project_loop_finish.set_change_status",
                       side_effect=StoryLoopError("simulated finalization crash")):
                with self.assertRaisesRegex(StoryLoopError, "simulated finalization"):
                    run_change(root, change_id, services)

            result = run_change(root, change_id, services)
            self.assertEqual(result["status"], "PASSED")


class E3MergeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        repository(self.root)
        bootstrap_project(self.root, {"test_commands": []})
        change = create_change(self.root, "orders", product(),
                               now=lambda: datetime(2026, 10, 28, tzinfo=timezone.utc))
        self.feature_id = change["change_id"]
        approve_change(self.root, self.feature_id)

        def collide(project, context):
            (project / "pom.xml").write_text("<project><dependency>trial</dependency></project>\n",
                                              encoding="utf-8")
            return {"summary": "needs dependency", "questions": []}

        result = run_change(self.root, self.feature_id, LoopServices(
            generate_smoke=lambda project, change: smoke_payload(self.feature_id),
            implement=collide,
            review=lambda project, context: {"verdict": "pass", "summary": "ok",
                                             "findings": [], "questions": []}))
        self.assertEqual(result["status"], "BASE_CHANGE_REQUIRED")
        self.base_id = result["blocked_by"]
        self.feature_head = git(self.root, "rev-parse", "HEAD")
        self.source_branch = load_baseline(self.root)["source_branch"]
        git(self.root, "switch", self.source_branch)
        confirm_legacy_base_build(self.root, [], "StoryLoop Test")
        approve_change(self.root, self.base_id)

    def advance_base(self, *, completed=True, valid_receipt=True, conflict=False):
        baseline = load_baseline(self.root)
        (self.root / "pom.xml").write_text("<project><dependency>accepted</dependency></project>\n",
                                            encoding="utf-8")
        git(self.root, "add", "pom.xml")
        if conflict:
            target = self.root / "openspec/changes" / self.feature_id / "proposal.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("conflicting source proposal\n", encoding="utf-8")
            git(self.root, "add", target.relative_to(self.root).as_posix())
        git(self.root, "commit", "-m", "base dependency")
        self.source_head = git(self.root, "rev-parse", "HEAD")
        candidate = _next_baseline(self.root, baseline, load_project_config(self.root)["profile"],
                                   self.source_head, "1.0.1")
        write_json(self.root / ".storyloop/baseline.json", candidate)
        base = load_json(self.root / ".storyloop/state/changes" / f"{self.base_id}.json")
        if completed:
            base["status"] = "PASSED"
            write_json(self.root / ".storyloop/state/changes" / f"{self.base_id}.json", base)
        run = {"status": "PASSED" if completed else "IMPLEMENTING",
               "change_id": self.base_id, "integration_commit": self.source_head,
               "contract_digest": base["approval"]["contract_digest"],
               "source_branch": self.source_branch}
        write_json(self.root / ".storyloop/state/runs" / f"{self.base_id}.json", run)
        receipt = {"version": 2, "status": "ACCEPTED", "proposal_id": self.base_id,
                   "integration_commit": self.source_head,
                   "contract_digest": base["approval"]["contract_digest"],
                   "new_baseline_version": "1.0.1" if valid_receipt else "9.9.9",
                   "candidate_baseline_digest": _dict_digest(candidate)}
        write_json(self.root / ".storyloop/state/base-upgrades" / f"{self.base_id}.json", receipt)
        git(self.root, "switch", f"feature/{self.feature_id}")

    def test_incomplete_base_cannot_merge_into_feature(self):
        self.advance_base(completed=False)
        state = load_run_state(self.root, self.feature_id)
        with self.assertRaises(StoryLoopError):
            _follow_base_upgrade(self.root, state)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.feature_head)

    def test_accepted_base_merges_source_with_two_parents(self):
        self.advance_base()
        state = load_run_state(self.root, self.feature_id)
        _follow_base_upgrade(self.root, state)
        parents = git(self.root, "rev-list", "--parents", "-n", "1", "HEAD").split()
        self.assertEqual(parents[1:], [self.feature_head, self.source_head])
        self.assertEqual(state["source_head"], self.source_head)
        _follow_base_upgrade(self.root, state)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), parents[0])

    def test_run_merges_base_before_verifying_new_foundation(self):
        self.advance_base()
        result = run_change(self.root, self.feature_id, LoopServices(
            generate_smoke=lambda project, change: self.fail("old smoke must not regenerate"),
            implement=lambda project, context: self.fail("old candidate must not run"),
            review=lambda project, context: self.fail("old review must not run")))
        self.assertEqual(result["status"], "BASE_CHANGE_REQUIRED")
        self.assertEqual(len(git(self.root, "rev-list", "--parents", "-n", "1", "HEAD").split()), 3)

    def test_receipt_version_mismatch_blocks_merge(self):
        self.advance_base(valid_receipt=False)
        with self.assertRaises(StoryLoopError):
            _follow_base_upgrade(self.root, load_run_state(self.root, self.feature_id))
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.feature_head)

    def test_dirty_feature_preserves_unowned_file(self):
        self.advance_base()
        (self.root / "personal.txt").write_text("keep me\n", encoding="utf-8")
        with self.assertRaises(StoryLoopError):
            _follow_base_upgrade(self.root, load_run_state(self.root, self.feature_id))
        self.assertEqual((self.root / "personal.txt").read_text(encoding="utf-8"), "keep me\n")
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.feature_head)

    def test_merge_conflict_preserves_workspace_for_human(self):
        self.advance_base(conflict=True)
        with self.assertRaises(StoryLoopError) as caught:
            _follow_base_upgrade(self.root, load_run_state(self.root, self.feature_id))
        self.assertEqual(caught.exception.code, "WORKSPACE_TAINTED")
        self.assertEqual(git(self.root, "branch", "--show-current"), f"feature/{self.feature_id}")
        self.assertIn("AA openspec/changes", git(self.root, "status", "--porcelain"))


if __name__ == "__main__":
    unittest.main()
