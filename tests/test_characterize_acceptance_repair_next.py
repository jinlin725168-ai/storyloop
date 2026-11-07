"""Existing repair outcomes that must survive a change to the success hint."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from storyloop.acceptance_repair import repair_smoke
from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project, load_project_config
from storyloop.project_loop import load_run_state, run_change
from storyloop.storage import write_json
from tests.routing_support import legacy_create_change as create_change
from tests.test_baseline_behavior import git, repository
from tests.test_product_spec import rich_product
from tests.test_smoke_repair import defective_payload, good_payload, services, weak_payload


NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)


class AcceptanceRepairCharacterizationTests(unittest.TestCase):
    def _exhausted_run(self, root: Path) -> tuple[str, str]:
        repository(root)
        bootstrap_project(root, {"test_commands": []})
        config = load_project_config(root)
        config["max_attempts"] = 1
        write_json(root / ".storyloop/config.json", config)
        value = rich_product()
        value["questions"] = []
        change_id = create_change(root, "orders", value, now=lambda: NOW)["change_id"]
        approve_change(root, change_id, now=lambda: NOW)
        result = run_change(root, change_id, services(change_id, lambda p, c: defective_payload(change_id)))
        self.assertEqual(result["status"], "BUDGET_EXHAUSTED")
        return change_id, load_run_state(root, change_id)["red_evidence_sha256"]

    def test_success_preserves_result_and_saved_red_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id, old_red = self._exhausted_run(root)
            result = repair_smoke(root, change_id, "incorrect acceptance assertion",
                                  services(change_id, lambda p, c: good_payload(change_id)),
                                  now=lambda: NOW)

            self.assertEqual(result["status"], "ACCEPTANCE_REPAIRED")
            self.assertEqual(result["change_id"], change_id)
            self.assertEqual(result["commit"], git(root, "rev-parse", "HEAD"))
            state = load_run_state(root, change_id)
            self.assertEqual(state["status"], "IMPLEMENTING")
            self.assertEqual(state["attempt"], 0)
            self.assertNotEqual(state["red_evidence_sha256"], old_red)
            self.assertEqual(state["repairs"][0]["commit"], result["commit"])

    def test_unproven_red_keeps_existing_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id, _ = self._exhausted_run(root)
            result = repair_smoke(root, change_id, "suite does not test new behavior",
                                  services(change_id, lambda p, c: weak_payload(change_id)),
                                  now=lambda: NOW)

            self.assertEqual(result["status"], "EXISTING_GREEN")
            state = load_run_state(root, change_id)
            self.assertEqual(state["status"], "EXISTING_GREEN")
            self.assertEqual(state["attempt"], 0)
            self.assertNotIn("red_evidence_sha256", state)


if __name__ == "__main__":
    unittest.main()
