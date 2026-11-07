"""Default draft behavior preserved while Python scratch defaults expand."""

import json
import unittest

from storyloop.onboarding_templates import render


class OnboardingScratchCharacterization(unittest.TestCase):
    def test_python_draft_retains_existing_cache_defaults(self):
        draft = json.loads(render(["python"])["acceptance/storyloop-settings.json"])
        for name in ("__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"):
            with self.subTest(name=name):
                self.assertIn(name, draft["scratch_paths"])

    def test_non_python_draft_has_no_python_scratch_defaults(self):
        draft = json.loads(render(["maven"])["acceptance/storyloop-settings.json"])
        self.assertEqual(draft["scratch_paths"], [])


if __name__ == "__main__":
    unittest.main()
