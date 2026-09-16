from pathlib import Path
import tempfile
import unittest

from storyloop.models import StoryLoopError
from storyloop.project_profile import is_ci_path, validate_project_settings


class CiPathSafetyTests(unittest.TestCase):
    def test_dot_prefixed_ci_paths_remain_protected_after_normalization(self):
        self.assertTrue(is_ci_path(".github/workflows/ci.yml"))
        self.assertTrue(is_ci_path(".gitlab-ci.yml"))
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(StoryLoopError, "CI"):
                validate_project_settings(
                    Path(folder), {"module_roots": [".github/workflows"]}
                )


if __name__ == "__main__":
    unittest.main()
