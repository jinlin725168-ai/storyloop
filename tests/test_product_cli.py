from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.baseline import bootstrap_project
from storyloop.cli import dispatch, parser
from storyloop.changes import load_change
from tests.support import product
from tests.test_baseline_behavior import repository


class ProductCliTests(unittest.TestCase):
    def test_help_surface_includes_product_change_and_ci_commands(self):
        help_text = parser().format_help()
        self.assertIn("change", help_text)
        self.assertIn("ci", help_text)

    def test_change_uses_ai_product_result_and_approve_runs_automatically(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
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


if __name__ == "__main__":
    unittest.main()
