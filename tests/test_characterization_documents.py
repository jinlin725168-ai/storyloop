"""Documentation needs no characterization lock (2026-11-16 L3 on sqlite-utils).

A plan that also updated docs/cli.rst and docs/cli-reference.rst blocked with "Characterization
coverage declaration omits required paths": documents were required to have behaviour locks.
"""

from pathlib import Path
import tempfile
import unittest

from storyloop.project_loop_gates import needs_characterization, uncharacterized_paths
from tests.test_baseline_behavior import git


class CharacterizationDocumentTests(unittest.TestCase):
    def test_documents_and_tests_need_no_lock(self):
        roots = ["tests"]
        self.assertTrue(needs_characterization("sqlite_utils/cli.py", roots))
        for path in ("docs/cli.rst", "docs/cli-reference.rst", "README.md", "tests/test_cli.py"):
            with self.subTest(path=path):
                self.assertFalse(needs_characterization(path, roots))

    def test_uncharacterized_paths_skip_documents(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            git(root, "init", "-q")
            git(root, "config", "user.email", "storyloop@example.invalid")
            git(root, "config", "user.name", "StoryLoop Test")
            for name in ("app/x.py", "app/y.py", "docs/guide.rst", "README.md"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text("x\n", encoding="utf-8")
            git(root, "add", ".")
            git(root, "commit", "-q", "-m", "base")
            state = {"characterization": {"status": "GREEN", "touched": ["app/x.py"]},
                     "contract_commit": git(root, "rev-parse", "HEAD").strip()}
            boundary = {"allowed": [{"path": p} for p in
                                    ("app/x.py", "app/y.py", "docs/guide.rst", "README.md")]}
            found = uncharacterized_paths(root, state, boundary, {"profile": {"test_roots": ["tests"]}})
        self.assertEqual(found, ["app/y.py"])


if __name__ == "__main__":
    unittest.main()
