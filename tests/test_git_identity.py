"""The loop commits with the identity git itself would use, including the user's global config."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from storyloop.models import StoryLoopError
from storyloop.project_git import commit_paths


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


class GlobalIdentityTests(unittest.TestCase):
    """Found following the tutorial: with the identity only in ~/.gitconfig (the usual setup), the engine
    ran git without HOME, git saw no identity, and the run stopped at an unexplained GIT_BLOCKED."""

    def _repository_without_local_identity(self, folder: Path) -> Path:
        root = folder / "repo"
        root.mkdir()
        _git(root, "init", "-q")
        (root / "a.txt").write_text("a\n", encoding="utf-8")
        _git(root, "add", "a.txt")
        _git(root, "-c", "user.name=Setup", "-c", "user.email=setup@example.invalid", "commit", "-q", "-m", "init")
        (root / "b.txt").write_text("b\n", encoding="utf-8")
        return root

    def _home(self, folder: Path, config: str) -> dict:
        home = folder / "home"
        home.mkdir()
        (home / ".gitconfig").write_text(config, encoding="utf-8")
        return {"HOME": str(home), "USERPROFILE": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
                "GIT_CONFIG_NOSYSTEM": "1"}

    def test_an_identity_set_only_in_the_global_config_is_used(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            root = self._repository_without_local_identity(folder)
            home = self._home(folder, "[user]\n\tname = Global User\n\temail = global@example.invalid\n")
            with mock.patch.dict(os.environ, home):
                commit_paths(root, ["b.txt"], "add b")
            author = subprocess.run(["git", "log", "-1", "--format=%an <%ae>"], cwd=root,
                                    capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(author, "Global User <global@example.invalid>")

    def test_a_missing_identity_says_so(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            root = self._repository_without_local_identity(folder)
            home = self._home(folder, "")
            with mock.patch.dict(os.environ, {**home, "EMAIL": ""}), \
                    self.assertRaises(StoryLoopError) as caught:
                commit_paths(root, ["b.txt"], "add b")
            self.assertEqual(caught.exception.code, "GIT_BLOCKED")
            self.assertIn("user.email", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
