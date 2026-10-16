import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.models import StoryLoopError
from storyloop.routing_evidence import (
    MAX_GIT_OUTPUT, _run, actual_scope, measure_scope, reference_count,
)
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
        with self.assertRaises(StoryLoopError):
            measure_scope(self.root, self.head, scope, validate_policy(None))

    def test_unknown_symbol_association_cannot_complete_scope(self):
        scope = {"touched": ["app/service.py"], "creates": [], "interfaces": [],
                 "symbols": [{"path": "app/service.py", "symbol": "serve"}]}
        with patch("storyloop.routing_evidence._symbol_at_path", return_value=None):
            result = measure_scope(self.root, self.head, scope, validate_policy(None))
        self.assertIsNone(result["signals"]["max_references"])
        self.assertFalse(result["signals"]["complete"])

    def test_risk_paths_use_directory_and_glob_matching(self):
        scope = {"touched": ["app/service.py"], "creates": [], "interfaces": [], "symbols": []}
        policy = validate_policy({"sensitive_paths": ["app"], "guard_paths": ["app/**/*.py"]})
        result = measure_scope(self.root, self.head, scope, policy)
        self.assertEqual(result["signals"]["risks"], {"sensitive": "yes", "guard": "yes"})

    def test_empty_touched_scope_has_known_zero_references(self):
        scope = {"touched": [], "creates": ["app/new.py"],
                 "interfaces": [], "symbols": []}
        result = measure_scope(self.root, self.head, scope, validate_policy(None))
        self.assertEqual(result["signals"]["max_references"], 0)
        self.assertTrue(result["signals"]["complete"])

    def test_scope_deduplicates_symbol_references_and_marks_policy_risks(self):
        paths = []
        (self.root / "app/service.py").write_text("TARGET = 1\n", encoding="utf-8")
        for index in range(29):
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
        failure = {"returncode": 2, "stdout": "", "stderr": "bad revision", "timed_out": False,
                   "truncated": False, "error": "", "contained": True}
        with patch("storyloop.routing_evidence.run_process", return_value=failure):
            self.assertIsNone(reference_count(self.root, self.head, "TARGET"))

    def test_reference_count_rejects_invalid_symbol(self):
        for symbol in ("", "two\nlines", "x" * 1001):
            with self.subTest(symbol=symbol[:10]), self.assertRaises(StoryLoopError):
                reference_count(self.root, self.head, symbol)

    def test_reference_count_returns_unknown_for_timeout_and_truncation(self):
        base = {"returncode": None, "stdout": "", "stderr": "", "timed_out": False,
                "truncated": False, "error": "", "contained": True}
        with patch("storyloop.routing_evidence.run_process",
                   return_value={**base, "timed_out": True}):
            self.assertIsNone(reference_count(self.root, self.head, "TARGET"))
        with patch("storyloop.routing_evidence.run_process",
                   return_value={**base, "returncode": 0, "truncated": True}):
            self.assertIsNone(reference_count(self.root, self.head, "TARGET"))

    def test_git_capture_is_really_bounded(self):
        (self.root / "large.txt").write_text("x" * (MAX_GIT_OUTPUT + 100_000), encoding="utf-8")
        git(self.root, "add", "large.txt")
        git(self.root, "commit", "-q", "-m", "large blob")
        result = _run(self.root, "show", "HEAD:large.txt")
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["stdout"].encode()), MAX_GIT_OUTPUT)

    def test_workspace_config_is_explicitly_digest_bound(self):
        profile = {"test_roots": ["app"], "generated_paths": [], "protected_paths": []}
        config = self.root / ".storyloop/config.json"
        config.parent.mkdir()
        config.write_text(json.dumps({"profile": profile}), encoding="utf-8")
        scope = {"touched": ["app/service.py"], "creates": [], "interfaces": [], "symbols": []}
        result = measure_scope(self.root, self.head, scope, validate_policy(None))
        item = next(value for value in result["evidence"] if value["field"] == "classification")
        canonical = json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(item, {
            "field": "classification",
            "value": {"kind": "workspace_config", "digest": hashlib.sha256(canonical).hexdigest(),
                      "commit_bound": False},
            "source": "engine", "locations": [".storyloop/config.json"], "commit": None,
        })
        self.assertEqual(result["signals"]["existing_files"], 0)

    def test_fallback_classification_ignores_workspace_only_layout(self):
        scope = {"touched": [], "creates": [".github/workflows/new.yml"],
                 "interfaces": [], "symbols": []}
        before = measure_scope(
            self.root, self.head, scope, validate_policy(None),
        )
        workflow = self.root / ".github/workflows/new.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text("name: workspace only\n", encoding="utf-8")
        after = measure_scope(
            self.root, self.head, scope, validate_policy(None),
        )
        self.assertEqual(before["signals"]["created_files"], 1)
        self.assertEqual(before, after)

    def test_linked_git_worktree_is_supported(self):
        with tempfile.TemporaryDirectory() as parent:
            linked = Path(parent) / "linked"
            git(self.root, "worktree", "add", "-q", "--detach", str(linked), self.head)
            self.assertEqual(reference_count(linked, self.head, "serve"), 1)

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
