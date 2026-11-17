"""New tests added to an existing test file need no regression-update declaration.

2026-11-16 L3 on sqlite-utils: the implementer appended five tests to tests/test_cli.py
(53 lines added, none removed) and the run stopped at BOUNDARY_VIOLATION because every edit of
an existing test file counted as an undeclared regression update.
"""

from pathlib import Path
import tempfile
import unittest

from storyloop.regression_updates import append_only_test, undeclared_existing_tests
from tests.test_baseline_behavior import git

OLD = (
    "import pytest\n\n\n"
    "@pytest.fixture\ndef db():\n    return 1\n\n\n"
    "def test_old(db):\n    assert db == 1\n"
)


class AppendOnlyTests(unittest.TestCase):
    def test_new_definitions_after_unchanged_old_ones_are_additions(self):
        new = OLD + "\n\nimport json\n\n\ndef test_new(db):\n    assert json.dumps(db) == '1'\n"
        self.assertTrue(append_only_test("tests/test_cli.py", OLD, new))
        decorated = OLD + "\n\n@pytest.mark.parametrize('x', [1])\ndef test_param(x):\n    assert x\n"
        self.assertTrue(append_only_test("tests/test_cli.py", OLD, decorated))

    def test_changes_that_could_weaken_old_tests_are_not_additions(self):
        cases = {
            "edited body": OLD.replace("assert db == 1", "assert db"),
            "early return": OLD.replace("def test_old(db):\n", "def test_old(db):\n    return\n"),
            "skip decorator": OLD.replace("def test_old(db):", "@pytest.mark.skip\ndef test_old(db):"),
            "module skip": OLD + "\npytestmark = pytest.mark.skip\n",
            "autouse fixture": OLD + "\n@pytest.fixture(autouse=True)\ndef patch():\n    yield\n",
            "shadowed test": OLD + "\ndef test_old(db):\n    pass\n",
            "removed test": OLD.replace("def test_old(db):\n    assert db == 1\n", ""),
            "syntax error": OLD + "\ndef broken(:\n",
        }
        for name, new in cases.items():
            with self.subTest(name=name):
                self.assertFalse(append_only_test("tests/test_cli.py", OLD, new))
        appended = OLD + "\ndef test_new():\n    pass\n"
        self.assertFalse(append_only_test("tests/conftest.py", OLD, appended))
        self.assertFalse(append_only_test("tests/snapshot.json", "{}\n", "{}\n\n"))

    def test_boundary_reports_only_edits_of_old_tests(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            git(root, "init", "-q")
            git(root, "config", "user.email", "storyloop@example.invalid")
            git(root, "config", "user.name", "StoryLoop Test")
            (root / "tests").mkdir()
            for name in ("test_added.py", "test_edited.py"):
                (root / "tests" / name).write_text(OLD, encoding="utf-8")
            git(root, "add", "tests")
            git(root, "commit", "-q", "-m", "old tests")
            commit = git(root, "rev-parse", "HEAD").strip()
            (root / "tests/test_added.py").write_text(OLD + "\ndef test_new():\n    assert True\n",
                                                      encoding="utf-8")
            (root / "tests/test_edited.py").write_text(OLD.replace("== 1", "== 2"), encoding="utf-8")
            boundary = {"allowed": [{"path": "tests/test_added.py"}, {"path": "tests/test_edited.py"}]}
            found = undeclared_existing_tests(root, {"contract_commit": commit},
                                              {"profile": {"test_roots": ["tests"]}},
                                              boundary, {"regression_updates": []})
        self.assertEqual(found, ["tests/test_edited.py"])


if __name__ == "__main__":
    unittest.main()
