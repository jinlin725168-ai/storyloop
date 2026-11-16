"""The repository's own CI keeps a red main or nightly run visible."""

from pathlib import Path
import unittest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"


class CiWorkflowTests(unittest.TestCase):
    def test_red_main_or_nightly_run_opens_a_ci_red_issue(self):
        # 2026-11-16: the nightly full suite failed 8 of 10 runs unnoticed, hiding a regression.
        text = WORKFLOW.read_text(encoding="utf-8")
        alert = text[text.index("  alert:"):]
        self.assertIn("needs.result.result == 'failure'", alert)
        self.assertIn("github.event_name != 'pull_request'", alert)
        self.assertIn("issues: write", alert)
        self.assertIn("--label ci-red", alert)
        self.assertIn("gh issue comment", alert)


if __name__ == "__main__":
    unittest.main()
