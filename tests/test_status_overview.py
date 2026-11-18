"""Cross-change overview (roadmap F3): `status <project>` with no --change."""
import io
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from storyloop.cli import dispatch, parser
from storyloop.cli_output import display
from tests.routing_support import legacy_create_change as create_change
from tests.support import product
from tests.test_baseline_behavior import repository

NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


class StatusOverviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        repository(self.root)
        bootstrap_project(self.root)

    def test_overview_groups_changes_by_who_they_wait_on_with_next_commands(self):
        draft = create_change(self.root, "idea one", product(), now=lambda: NOW)["change_id"]
        approved = create_change(self.root, "idea two", product(), now=lambda: NOW)["change_id"]
        approve_change(self.root, approved, now=lambda: NOW)
        result = dispatch(parser().parse_args(["status", str(self.root)]))
        self.assertEqual(result["status"], "OVERVIEW")
        rows = {row["change_id"]: row for row in result["changes"]}
        self.assertEqual(set(rows), {draft, approved})
        self.assertEqual(rows[draft]["waiting"], "human")
        self.assertTrue(rows[draft]["next"])
        self.assertEqual(sum(result["counts"].values()), 2)
        output = io.StringIO()
        with redirect_stdout(output):
            display(result, False)
        self.assertIn("需要你决定", output.getvalue())
        self.assertIn(draft, output.getvalue())

    def test_overview_of_a_project_without_changes_says_how_to_start(self):
        result = dispatch(parser().parse_args(["status", str(self.root)]))
        self.assertEqual(result["changes"], [])
        output = io.StringIO()
        with redirect_stdout(output):
            display(result, False)
        self.assertIn("storyloop change", output.getvalue())


if __name__ == "__main__":
    unittest.main()
