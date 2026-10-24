"""After a change merges, the acceptance role proposes system-smoke updates; a human applies them."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.onboarding_cli import bootstrap_with_project_checks as bootstrap_project
from tests.routing_support import legacy_create_change as create_change
from storyloop.models import StoryLoopError
from storyloop.project_loop import LoopServices, run_change
from storyloop.system_smoke import (SYSTEM_SMOKE_DIR, apply_smoke_update, load_system_smoke,
                                    propose_smoke_update)
from tests.support import product
from tests.test_baseline_behavior import git, repository
from tests.test_project_loop import smoke_payload

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)


def _passed_change():
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    repository(root)
    bootstrap_project(root, {"unit_commands": []})
    state = create_change(root, "orders", product(), now=lambda: NOW)
    change_id = state["change_id"]
    approve_change(root, change_id, now=lambda: NOW)

    def implement(project, context):
        target = project / "modules/orders/service.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("VALUE = 1\n", encoding="utf-8")
        return {"summary": "done", "questions": []}

    services = LoopServices(generate_smoke=lambda project, state: smoke_payload(change_id), implement=implement,
                            review=lambda project, context: {"verdict": "pass", "summary": "ok", "findings": [],
                                                             "questions": []})
    result = run_change(root, change_id, services)
    assert result["status"] == "PASSED", result
    return temporary, root, change_id


def _proposal(exit_code: int):
    def propose(project, context):
        assert context["mode"] == "smoke-maintenance", context
        assert context["change"]["change_id"], context
        assert "acceptance_suite" in context and "system_smoke" in context, context
        return {"files": [{"path": "core_orders.py", "content": f"raise SystemExit({exit_code})\n"}],
                "manifest": {"version": 1, "tests": [{"id": "core-orders", "description": "订单主路径",
                                                       "command": ["{python}", f"{SYSTEM_SMOKE_DIR}/core_orders.py"],
                                                       "timeout_seconds": 30}]},
                "lessons_used": [{"id": "L-0001", "how": "checked"}], "errata": [],
                "rationale": "订单是核心链路，把变更验收里的主路径并入系统冒烟。"}
    return propose


class SmokeMaintenanceTests(unittest.TestCase):
    def test_propose_writes_a_draft_and_apply_installs_verifies_and_commits(self):
        temporary, root, change_id = _passed_change()
        try:
            proposed = propose_smoke_update(root, change_id, agent=_proposal(0))
            self.assertEqual(proposed["status"], "SMOKE_PROPOSED")
            draft = Path(proposed["draft"])
            self.assertTrue((draft / "manifest.json").is_file())
            self.assertTrue((draft / "core_orders.py").is_file())
            self.assertIn("订单", (draft / "rationale.md").read_text(encoding="utf-8"))
            self.assertIsNone(load_system_smoke(root))  # nothing installed until a human applies

            applied = apply_smoke_update(root, change_id)
            self.assertEqual(applied["status"], "SMOKE_APPLIED")
            self.assertEqual(load_system_smoke(root)["tests"][0]["id"], "core-orders")
            self.assertEqual(git(root, "log", "--format=%s", "-1"), f"test(smoke): update system smoke for {change_id}")
            self.assertEqual(git(root, "status", "--porcelain"), "")
            self.assertFalse(draft.exists())
        finally:
            temporary.cleanup()

    def test_a_red_proposal_is_not_applied_and_the_previous_suite_survives(self):
        temporary, root, change_id = _passed_change()
        try:
            propose_smoke_update(root, change_id, agent=_proposal(0))
            apply_smoke_update(root, change_id)
            head = git(root, "rev-parse", "HEAD")
            propose_smoke_update(root, change_id, agent=_proposal(1))
            result = apply_smoke_update(root, change_id)
            self.assertEqual(result["status"], "SMOKE_UPDATE_FAILED")
            self.assertEqual(git(root, "rev-parse", "HEAD"), head)
            self.assertEqual(git(root, "status", "--porcelain"), "")
            self.assertEqual(load_system_smoke(root)["tests"][0]["id"], "core-orders")
            self.assertEqual((root / SYSTEM_SMOKE_DIR / "core_orders.py").read_text(encoding="utf-8"),
                             "raise SystemExit(0)\n")
        finally:
            temporary.cleanup()

    def test_apply_needs_a_draft_and_a_clean_tree(self):
        temporary, root, change_id = _passed_change()
        try:
            with self.assertRaisesRegex(StoryLoopError, "draft"):
                apply_smoke_update(root, change_id)
            propose_smoke_update(root, change_id, agent=_proposal(0))
            (root / "dirty.txt").write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(StoryLoopError, "clean"):
                apply_smoke_update(root, change_id)
        finally:
            temporary.cleanup()

    def test_a_cloned_repository_adopts_the_committed_suite_at_init(self):
        temporary, root, change_id = _passed_change()
        try:
            propose_smoke_update(root, change_id, agent=_proposal(0))
            apply_smoke_update(root, change_id)
            (root / ".storyloop/state/system-smoke.json").unlink()
            with self.assertRaisesRegex(StoryLoopError, "protection"):
                load_system_smoke(root)
            import shutil
            shutil.rmtree(root / ".storyloop")
            bootstrap_project(root, {"unit_commands": []})
            self.assertEqual(load_system_smoke(root)["tests"][0]["id"], "core-orders")
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
