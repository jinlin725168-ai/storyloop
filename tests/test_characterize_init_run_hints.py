"""Preserved next action for an unrelated initialization failure."""

import json
import subprocess
import sys
import unittest

from tests.lesson_support import cleanup_repo, git_repo


class InitRunHintsCharacterizationTests(unittest.TestCase):
    def test_existing_baseline_keeps_generic_next(self):
        project = git_repo()
        self.addCleanup(cleanup_repo, project)
        baseline = project / ".storyloop" / "baseline.json"
        baseline.parent.mkdir()
        baseline.write_text("{}\n", encoding="utf-8")

        result = subprocess.run(
            [sys.executable, "-m", "storyloop", "init", str(project)],
            capture_output=True, text=True, check=False,
        )
        payload = json.loads(result.stderr)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(payload["message"], "StoryLoop baseline already exists")
        self.assertEqual(
            payload["next"],
            "检查错误并重试原命令；此时尚未创建变更，无可查看的 change id",
        )


if __name__ == "__main__":
    unittest.main()
