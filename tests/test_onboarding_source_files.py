"""Keep StoryLoop's dogfood smoke mirror in sync with engine modules."""

from collections import Counter
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXECUTOR = "acceptance/tools/python_smoke.py"


def source_mismatch(root: Path) -> tuple[list[str], list[str]]:
    """Compare the reviewed mirror inventory with engine sources in a project copy."""
    manifest = json.loads((root / "acceptance/onboarding.json").read_text(encoding="utf-8"))
    recorded = sorted(manifest["executors"][EXECUTOR]["source_files"])
    sources = (root / "storyloop").rglob("*.py")
    expected = sorted(path.relative_to(root).as_posix() for path in
                      [*sources, root / "storyloop_cli.py"]
                      if path.is_file() and "__pycache__" not in path.parts)
    return (sorted((Counter(expected) - Counter(recorded)).elements()),
            sorted((Counter(recorded) - Counter(expected)).elements()))


class OnboardingSourceFilesTests(unittest.TestCase):
    def test_smoke_sources_equal_engine_python_files(self):
        missing, extra = source_mismatch(ROOT)
        self.assertFalse(missing or extra, f"source_files 不同步；缺失：{missing}；多余：{extra}")

    def test_source_inventory_runs_in_archive_without_git(self):
        with tempfile.TemporaryDirectory() as folder:
            clone = Path(folder)
            shutil.copytree(ROOT / "storyloop", clone / "storyloop")
            shutil.copy2(ROOT / "storyloop_cli.py", clone / "storyloop_cli.py")
            manifest = clone / "acceptance/onboarding.json"
            manifest.parent.mkdir()
            shutil.copy2(ROOT / "acceptance/onboarding.json", manifest)
            cache = clone / "storyloop/__pycache__"
            cache.mkdir(exist_ok=True)
            (cache / "stale.py").write_text("", encoding="utf-8")
            self.assertFalse((clone / ".git").exists())
            missing, extra = source_mismatch(clone)
            self.assertFalse(missing or extra, f"archive 副本清单不同步；缺失：{missing}；多余：{extra}")
