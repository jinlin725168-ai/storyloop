from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from storyloop.baseline import bootstrap_project
from storyloop.changes import create_change, load_change, revise_change
from storyloop.models import StoryLoopError
from tests.support import product
from tests.test_baseline_behavior import git, repository


NOW = datetime(2026, 9, 16, 8, 30, tzinfo=timezone.utc)


class ChangeBehaviorTests(unittest.TestCase):
    def test_short_idea_becomes_a_structured_local_openspec_draft(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            draft = product()
            draft["questions"] = [{"id": "Q-1", "text": "允许重复提交吗？", "options": ["允许", "拒绝"]}]

            result = create_change(root, "增加会议室预订", draft, now=lambda: NOW)
            saved = load_change(root, result["change_id"])
            preview = root / ".storyloop/drafts" / result["change_id"]

            self.assertEqual(result["change_id"], "FEAT-20260916-001")
            self.assertEqual(saved["status"], "DRAFT")
            self.assertEqual(saved["revision"], 1)
            self.assertEqual(saved["idea"], "增加会议室预订")
            self.assertTrue((preview / "proposal.md").is_file())
            stories = json.loads((preview / "stories.json").read_text(encoding="utf-8"))
            acceptance = json.loads((preview / "acceptance.json").read_text(encoding="utf-8"))
            trace = json.loads((preview / "traceability.json").read_text(encoding="utf-8"))
            self.assertNotIn("acceptance", stories["stories"][0])
            self.assertEqual(acceptance["criteria"][0]["story_id"], "ST-1")
            self.assertEqual(trace["criteria"][0], {"criterion_id": "AC-1", "story_id": "ST-1", "tests": []})
            self.assertEqual(git(root, "status", "--porcelain=v1", "--untracked-files=all"), "")

    def test_revision_keeps_id_and_invalid_product_never_replaces_draft(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            created = create_change(root, "idea", product(), now=lambda: NOW)
            changed = deepcopy(product())
            changed["goal"] = "Updated business outcome"

            revised = revise_change(root, created["change_id"], changed, "refine outcome")

            self.assertEqual(revised["change_id"], created["change_id"])
            self.assertEqual(revised["revision"], 2)
            self.assertEqual(len(revised["history"]), 1)
            invalid = deepcopy(changed)
            invalid["stories"][0]["acceptance"].append(
                deepcopy(invalid["stories"][0]["acceptance"][0])
            )
            with self.assertRaisesRegex(StoryLoopError, "Duplicate acceptance"):
                revise_change(root, created["change_id"], invalid)
            self.assertEqual(load_change(root, created["change_id"])["revision"], 2)


if __name__ == "__main__":
    unittest.main()
