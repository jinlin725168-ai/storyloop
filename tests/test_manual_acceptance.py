"""Manual acceptance criteria pause a verified run until a human records acceptance."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from storyloop.changes import create_change
from storyloop.manual_acceptance import load_manual_acceptance, record_manual_acceptance
from storyloop.models import StoryLoopError
from storyloop.project_loop import LoopServices, load_run_state, run_change
from tests.support import product
from tests.test_baseline_behavior import repository
from tests.test_project_loop import smoke_payload

NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)


def mixed_product() -> dict:
    value = product()
    value["stories"][0]["acceptance"].append({
        "id": "AC-2", "given": "the page is open", "when": "a human looks at it",
        "then": "the layout matches the design", "kind": "manual"})
    return value


def services(change_id: str) -> LoopServices:
    def implement(project, context):
        target = project / "modules/orders/service.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("VALUE = 1\n", encoding="utf-8")
        return {"summary": "done", "questions": []}

    return LoopServices(
        generate_smoke=lambda project, state: smoke_payload(change_id),
        implement=implement,
        review=lambda project, context: {"verdict": "pass", "summary": "ok",
                                         "findings": [], "questions": []})


class ManualAcceptanceTests(unittest.TestCase):
    def _approved(self, root: Path) -> str:
        repository(root)
        bootstrap_project(root, {"test_commands": []})
        state = create_change(root, "orders", mixed_product(), now=lambda: NOW)
        approve_change(root, state["change_id"], now=lambda: NOW)
        return state["change_id"]

    def test_verified_run_pauses_until_manual_criteria_are_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = self._approved(root)

            paused = run_change(root, change_id, services(change_id))
            self.assertEqual(paused["status"], "MANUAL_ACCEPTANCE_REQUIRED", paused)
            self.assertNotIn("questions", load_run_state(root, change_id))
            self.assertEqual(paused["pending"], ["AC-2"])
            self.assertEqual(load_run_state(root, change_id)["status"], "MANUAL_ACCEPTANCE_REQUIRED")

            recorded = record_manual_acceptance(root, change_id, ["AC-2"], "layout checked", now=lambda: NOW)
            self.assertEqual(recorded["pending"], [])
            records = load_manual_acceptance(root, change_id)
            self.assertEqual(records[0]["criterion_id"], "AC-2")
            self.assertEqual(records[0]["candidate_sha256"],
                             load_run_state(root, change_id)["candidate_sha256"])

            done = run_change(root, change_id, services(change_id))
            self.assertEqual(done["status"], "PASSED", done)
            self.assertEqual(load_run_state(root, change_id)["manual_acceptance"][0]["note"],
                             "layout checked")
            report = json.loads(Path(done["report"]).read_text(encoding="utf-8"))
            human = [row for row in report["traceability"] if row["criterion"]["id"] == "AC-2"]
            self.assertEqual(human[0]["tests"][0]["result"]["status"], "ACCEPTED")
            self.assertEqual(human[0]["tests"][0]["note"], "layout checked")

    def test_acceptance_rejects_wrong_ids_and_wrong_moment(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = self._approved(root)
            with self.assertRaises(StoryLoopError):
                record_manual_acceptance(root, change_id, ["AC-2"], "too early", now=lambda: NOW)
            run_change(root, change_id, services(change_id))
            with self.assertRaises(StoryLoopError):
                record_manual_acceptance(root, change_id, ["AC-1"], "automated one", now=lambda: NOW)
            with self.assertRaises(StoryLoopError):
                record_manual_acceptance(root, change_id, ["AC-9"], "unknown", now=lambda: NOW)
            with self.assertRaises(StoryLoopError):
                record_manual_acceptance(root, change_id, ["AC-2"], "", now=lambda: NOW)


if __name__ == "__main__":
    unittest.main()
