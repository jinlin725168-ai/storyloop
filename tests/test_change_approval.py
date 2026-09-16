from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from storyloop.approvals import approve_change, materialize_approved_change
from storyloop.baseline import bootstrap_project
from storyloop.changes import create_change, load_change, revise_change
from storyloop.models import StoryLoopError
from tests.support import product
from tests.test_baseline_behavior import repository


NOW = datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc)


class ChangeApprovalTests(unittest.TestCase):
    def test_questions_block_approval_and_clean_contract_can_be_materialized(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            unresolved = product()
            unresolved["questions"] = [{"id": "Q-1", "text": "哪种策略？", "options": ["A", "B"]}]
            blocked = create_change(root, "idea", unresolved, now=lambda: NOW)
            with self.assertRaisesRegex(StoryLoopError, "questions"):
                approve_change(root, blocked["change_id"])

            clean = create_change(root, "another", product(), now=lambda: NOW)
            approved = approve_change(root, clean["change_id"], now=lambda: NOW)
            destination = materialize_approved_change(root, clean["change_id"])

            self.assertEqual(approved["status"], "APPROVED")
            self.assertEqual(len(approved["approval"]["contract_digest"]), 64)
            self.assertTrue((destination / "proposal.md").is_file())
            self.assertTrue((destination / "approval.json").is_file())

    def test_revision_invalidates_an_existing_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            created = create_change(root, "idea", product(), now=lambda: NOW)
            approve_change(root, created["change_id"], now=lambda: NOW)
            changed = deepcopy(product())
            changed["goal"] = "changed"

            revised = revise_change(root, created["change_id"], changed)

            self.assertEqual(revised["status"], "DRAFT")
            self.assertIsNone(load_change(root, created["change_id"])["approval"])


if __name__ == "__main__":
    unittest.main()
