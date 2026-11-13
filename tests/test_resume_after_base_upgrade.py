"""A feature paused at BASE_CHANGE_REQUIRED resumes after the base change is locked with base-update:
the run adopts the upgraded baseline head as its source head, so completion can still merge. Any
other movement of the source branch is still refused."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.base_upgrade import upgrade_baseline
from storyloop.baseline import bootstrap_project, load_baseline
from tests.routing_support import legacy_create_change as create_change
from storyloop.models import StoryLoopError
from storyloop.project_loop import LoopServices, load_run_state, run_change
from storyloop.project_loop_support import save_run
from tests.support import product
from tests.test_baseline_behavior import git, repository
from tests.test_project_loop import smoke_payload

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)
PASS = [sys.executable, "-c", "pass"]


def _repo():
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    repository(root)
    (root / "README.md").write_text("usage: add | list\n", encoding="utf-8")
    (root / "modules/orders").mkdir(parents=True)
    (root / "modules/orders/service.py").write_text("VALUE = 0\n", encoding="utf-8")
    git(root, "add", "."); git(root, "commit", "-q", "-m", "existing")
    bootstrap_project(root, {"unit_commands": [PASS]})
    state = create_change(root, "orders", product(), now=lambda: NOW)
    approve_change(root, state["change_id"], now=lambda: NOW)
    return temporary, root, state["change_id"]


def _services(change_id, touch_readme):
    def implement(project, context):
        (project / "modules/orders/service.py").write_text("VALUE = 1\n", encoding="utf-8")
        if touch_readme:
            (project / "README.md").write_text("usage: add | list | done\n", encoding="utf-8")
        return {"summary": "done", "questions": [], "design": "## Tests First\nnone\n"}

    return LoopServices(
        generate_smoke=lambda project, state: smoke_payload(change_id),
        implement=implement,
        review=lambda project, context: {"verdict": "pass", "summary": "ok", "findings": [], "questions": []},
    )


def _pause_on_readme(root, change_id):
    paused = run_change(root, change_id, _services(change_id, touch_readme=True))
    assert paused["status"] == "BASE_CHANGE_REQUIRED", paused
    # Preserve coverage of the historical unlinked base-update recovery path.
    state = load_run_state(root, change_id)
    state.pop("blocked_by", None)
    save_run(root, state)
    git(root, "checkout", "-q", "--", ".")
    return state["base_proposal"]


class ResumeAfterBaseUpgradeTests(unittest.TestCase):
    def test_a_locked_base_upgrade_moves_the_source_head_and_the_feature_still_merges(self):
        temporary, root, change_id = _repo()
        try:
            proposal = Path(_pause_on_readme(root, change_id)).stem
            git(root, "checkout", "-q", load_run_state(root, change_id)["source_branch"])
            (root / "README.md").write_text("usage: add | list | done\n", encoding="utf-8")
            git(root, "commit", "-q", "-am", "docs: README mentions done")
            from tests.test_base_upgrade import begin_legacy_receipt
            begin_legacy_receipt(root, root / ".storyloop/base-proposals" / f"{proposal}.json")
            self.assertEqual(upgrade_baseline(root, proposal)["status"], "BASELINE_UPGRADED")
            upgraded_head = load_baseline(root)["source_head"]
            git(root, "checkout", "-q", f"feature/{change_id}")
            git(root, "merge", "-q", "--no-edit", load_run_state(root, change_id)["source_branch"])

            result = run_change(root, change_id, _services(change_id, touch_readme=False))

            self.assertEqual(result["status"], "PASSED")
            state = load_run_state(root, change_id)
            self.assertEqual(state["source_head"], upgraded_head)
            self.assertEqual(state["base_upgrades"][0]["baseline_version"], "1.0.1")
            parents = git(root, "rev-list", "--parents", "-n", "1", state["merge_commit"]).split()
            self.assertEqual(parents[1], upgraded_head)  # no-ff merge onto the upgraded main
        finally:
            temporary.cleanup()

    def test_an_unlocked_move_of_the_source_branch_is_still_refused(self):
        temporary, root, change_id = _repo()
        try:
            _pause_on_readme(root, change_id)
            git(root, "checkout", "-q", load_run_state(root, change_id)["source_branch"])
            (root / "modules/orders/other.py").write_text("X = 1\n", encoding="utf-8")
            git(root, "add", "."); git(root, "commit", "-q", "-m", "unrelated")
            git(root, "checkout", "-q", f"feature/{change_id}")
            git(root, "merge", "-q", "--no-edit", load_run_state(root, change_id)["source_branch"])
            with self.assertRaisesRegex(StoryLoopError, "Source branch changed"):
                run_change(root, change_id, _services(change_id, touch_readme=False))
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
