from pathlib import Path
import tempfile
import unittest

from storyloop.models import StoryLoopError
from storyloop.project_git import _safe_path as safe_git_path
from storyloop.project_profile import validate_project_settings
from storyloop.smoke import _safe_relative as safe_smoke_path


class WindowsPathGuardTests(unittest.TestCase):
    def test_drive_and_unc_paths_are_never_repository_relative(self):
        for value in ("C:/Windows/Temp/escape.py", r"C:\Windows\Temp\escape.py",
                      r"\\server\share\escape.py", "//server/share/escape.py"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(StoryLoopError, "path|relative|Unsafe"):
                    safe_smoke_path(value)
                with self.assertRaisesRegex(StoryLoopError, "path|relative|Unsafe"):
                    safe_git_path(value)

    def test_project_settings_reject_absolute_windows_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            for value in ("C:/modules", r"\\server\share\modules"):
                with self.subTest(value=value):
                    with self.assertRaisesRegex(StoryLoopError, "relative"):
                        validate_project_settings(Path(folder), {"module_roots": [value]})


if __name__ == "__main__":
    unittest.main()
