from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.baseline import bootstrap_project
from storyloop.cli import dispatch, parser
from storyloop.changes import load_change
from tests.test_intake import assessed_product as product
from tests.routing_support import readiness_repository
from storyloop.readiness import inspect_readiness, approve_readiness
from tests.test_baseline_behavior import repository


class ProductCliTests(unittest.TestCase):
    def test_help_surface_includes_product_change_and_ci_commands(self):
        help_text = parser().format_help()
        self.assertIn("change", help_text)
        self.assertIn("ci", help_text)

    def test_change_uses_ai_product_result_and_approve_runs_automatically(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            readiness_repository(root)
            ready = inspect_readiness(root)
            approve_readiness(root, ready["digest"], "Local tests")
            args = parser().parse_args(["change", str(root), "--idea", "增加预订"])
            with patch("storyloop.cli.invoke_project_agent", return_value=product()):
                created = dispatch(args)
            self.assertEqual(created["status"], "DRAFT")
            self.assertEqual(load_change(root, created["change_id"])["product"]["goal"], product()["goal"])

            approved_args = parser().parse_args([
                "approve", str(root), "--change", created["change_id"]
            ])
            with patch("storyloop.cli.run_change", return_value={"status": "PASSED", "report": "report.json"}) as run:
                result = dispatch(approved_args)
            self.assertEqual(result["status"], "PASSED")
            run.assert_called_once_with(root.resolve(), created["change_id"])

    def test_a_draft_with_open_questions_says_where_they_are_and_not_to_approve_yet(self):
        """Found following the tutorial: `change --no-interactive` printed only DRAFT, and the hint said
        "satisfied? approve" while the checklist still had blocking items that make approve refuse."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            readiness_repository(root)
            ready = inspect_readiness(root)
            approve_readiness(root, ready["digest"], "Local tests")
            asking = product()
            asking["questions"] = [{"id": "Q1", "text": "Can a booking be cancelled?",
                                    "options": ["Yes", "No"], "recommended": "Yes", "why": "Frees rooms."}]
            args = parser().parse_args(["change", str(root), "--idea", "增加预订", "--no-interactive"])
            with patch("storyloop.cli.invoke_project_agent", return_value=asking):
                created = dispatch(args)
            self.assertIn("1 个待决问题", created["next"])
            self.assertIn("proposal.md", created["next"])
            self.assertNotIn("满意后", created["next"])
            status = dispatch(parser().parse_args(["status", str(root), "--change", created["change_id"]]))
            self.assertEqual(status["next"], created["next"])

            answered = product()
            with patch("storyloop.cli.invoke_project_agent", return_value=answered):
                revised = dispatch(parser().parse_args(["change", str(root), "--change", created["change_id"],
                                                        "--message", "Q1 选推荐项", "--no-interactive"]))
            self.assertIn("storyloop approve", revised["next"])


if __name__ == "__main__":
    unittest.main()
