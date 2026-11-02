"""Keep StoryLoop's dogfood smoke mirror in sync with tracked engine modules."""

from collections import Counter
import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "acceptance/onboarding.json"
EXECUTOR = "acceptance/tools/python_smoke.py"


class OnboardingSourceFilesTests(unittest.TestCase):
    def test_smoke_sources_equal_tracked_engine_python_files(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        recorded = sorted(manifest["executors"][EXECUTOR]["source_files"])
        tracked = subprocess.check_output(
            ["git", "ls-files", "-z", "--", "storyloop", "storyloop_cli.py"], cwd=ROOT,
        ).decode("utf-8").split("\0")
        expected = sorted(path for path in tracked if path == "storyloop_cli.py"
                          or path.startswith("storyloop/") and path.endswith(".py"))
        if recorded != expected:
            missing = sorted((Counter(expected) - Counter(recorded)).elements())
            extra = sorted((Counter(recorded) - Counter(expected)).elements())
            self.fail(f"source_files 与 Git 跟踪文件不同；缺失：{missing}；多余：{extra}")
