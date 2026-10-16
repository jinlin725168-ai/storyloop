from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from storyloop.models import StoryLoopError
from storyloop.routing_evidence import actual_scope, measure_scope, reference_count
from storyloop.routing_policy import validate_policy
from tests.routing_support import routing_repository
from tests.test_baseline_behavior import git


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        routing_repository(self.root)
        self.head = git(self.root, "rev-parse", "HEAD")

    def tearDown(self):
        self.folder.cleanup()

    def test_scope_counts_existing_and_created_business_files(self):
        scope = {"touched": ["app/service.py", "tests/test_service.py"],
                 "creates": ["app/new.py"], "interfaces": ["API", "API"], "symbols": []}
        result = measure_scope(self.root, self.head, scope, validate_policy(None))
        self.assertEqual(result["signals"], {
            "existing_files": 1, "created_files": 1, "interfaces": 1,
            "max_references": None, "risks": {"sensitive": "no", "guard": "no"},
            "complete": False,
        })

    def test_scope_rejects_parent_traversal(self):
        scope = {"touched": ["../outside.py"], "creates": [],
                 "interfaces": [], "symbols": []}
        with self.assertRaises(StoryLoopError):
            measure_scope(self.root, self.head, scope, validate_policy(None))

    def test_scope_rejects_wrong_existence_class_and_symlink_escape(self):
        outside = self.root.parent / "outside.py"
        outside.write_text("secret", encoding="utf-8")
        (self.root / "link.py").symlink_to(outside)
        cases = [
            {"touched": [], "creates": ["app/service.py"], "interfaces": [], "symbols": []},
            {"touched": ["app/missing.py"], "creates": [], "interfaces": [], "symbols": []},
            {"touched": [], "creates": ["link.py"], "interfaces": [], "symbols": []},
        ]
        for scope in cases:
            with self.subTest(scope=scope), self.assertRaises(StoryLoopError):
                measure_scope(self.root, self.head, scope, validate_policy(None))

    def test_scope_uses_commit_not_forged_worktree_content(self):
        (self.root / "app/service.py").write_text("FORGED_TOKEN\n", encoding="utf-8")
        scope = {"touched": ["app/service.py"], "creates": [], "interfaces": [],
                 "symbols": [{"path": "app/service.py", "symbol": "FORGED_TOKEN"}]}
        result = measure_scope(self.root, self.head, scope, validate_policy(None))
        self.assertEqual(result["signals"]["max_references"], 0)

    def test_empty_touched_scope_has_known_zero_references(self):
        scope = {"touched": [], "creates": ["app/new.py"],
                 "interfaces": [], "symbols": []}
        result = measure_scope(self.root, self.head, scope, validate_policy(None))
        self.assertEqual(result["signals"]["max_references"], 0)
        self.assertTrue(result["signals"]["complete"])

    def test_scope_deduplicates_symbol_references_and_marks_policy_risks(self):
        paths = []
        for index in range(30):
            path = self.root / "app" / f"ref {index}.py"
            path.write_text("TARGET = 1\nTARGET = 2\n", encoding="utf-8")
            paths.append(path.relative_to(self.root).as_posix())
        (self.root / "tests/test_service.py").write_text("TARGET = 1\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "references")
        head = git(self.root, "rev-parse", "HEAD")
        scope = {"touched": ["app/service.py"], "creates": [], "interfaces": [],
                 "symbols": [{"path": "app/service.py", "symbol": "TARGET"}]}
        policy = validate_policy({"sensitive_paths": ["app"], "guard_paths": ["app/service.py"]})
        result = measure_scope(self.root, head, scope, policy)
        self.assertEqual(result["signals"]["max_references"], 30)
        self.assertEqual(result["signals"]["risks"], {"sensitive": "yes", "guard": "yes"})
        hotspot = next(item for item in result["evidence"] if item["field"] == "hotspots")
        self.assertIn("app/service.py", hotspot["value"])
        git(self.root, "rm", paths[-1])
        git(self.root, "commit", "-q", "-m", "29 references")
        result = measure_scope(self.root, git(self.root, "rev-parse", "HEAD"), scope, policy)
        self.assertEqual(result["signals"]["max_references"], 29)

    def test_scope_rejects_invalid_symbol_contract(self):
        for symbol in ("", "two\nlines", "x" * 1001):
            scope = {"touched": ["app/service.py"], "creates": [], "interfaces": [],
                     "symbols": [{"path": "app/service.py", "symbol": symbol}]}
            with self.subTest(symbol=symbol[:10]), self.assertRaises(StoryLoopError):
                measure_scope(self.root, self.head, scope, validate_policy(None))

    def test_reference_count_distinguishes_no_match_from_git_failure(self):
        self.assertEqual(reference_count(self.root, self.head, "ABSENT"), 0)
        failure = subprocess.CompletedProcess([], 2, stdout=b"", stderr=b"bad revision")
        with patch("storyloop.routing_evidence.subprocess.run", return_value=failure):
            self.assertIsNone(reference_count(self.root, self.head, "TARGET"))

    def test_reference_count_rejects_invalid_symbol(self):
        for symbol in ("", "two\nlines", "x" * 1001):
            with self.subTest(symbol=symbol[:10]), self.assertRaises(StoryLoopError):
                reference_count(self.root, self.head, symbol)

    def test_reference_count_returns_unknown_for_timeout_and_truncation(self):
        with patch("storyloop.routing_evidence.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(["git"], 30)):
            self.assertIsNone(reference_count(self.root, self.head, "TARGET"))
        oversized = subprocess.CompletedProcess([], 0, stdout=b"x" * (1_048_577), stderr=b"")
        with patch("storyloop.routing_evidence.subprocess.run", return_value=oversized):
            self.assertIsNone(reference_count(self.root, self.head, "TARGET"))

    def test_actual_scope_reports_delete_rename_new_and_unknown_semantics(self):
        (self.root / "app/service.py").unlink()
        (self.root / "app/renamed.py").write_text("def serve(): pass\n", encoding="utf-8")
        (self.root / "new file.py").write_text("new\n", encoding="utf-8")
        result = actual_scope(self.root, self.head)
        self.assertEqual(result["touched"], ["app/service.py"])
        self.assertEqual(result["creates"], ["app/renamed.py", "new file.py"])
        self.assertEqual(result["interfaces"], [])
        self.assertEqual(result["symbols"], [])
        self.assertEqual(result["unknown_fields"], ["interfaces", "symbols"])

    def test_actual_scope_ignores_ignored_untracked_files(self):
        (self.root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
        git(self.root, "add", ".gitignore")
        git(self.root, "commit", "-q", "-m", "ignore")
        contract = git(self.root, "rev-parse", "HEAD")
        (self.root / "ignored.txt").write_text("ignored\n", encoding="utf-8")
        self.assertEqual(actual_scope(self.root, contract)["creates"], [])


if __name__ == "__main__":
    unittest.main()
