import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


def product(goal="Reservation"):
    return {"version": 1, "goal": goal, "non_goals": [], "questions": [], "stories": [
        {"id": "S1", "title": "Book room", "as_a": "employee", "want": "book room",
         "so_that": "meet", "rules": ["No overlap"], "out_of_scope": [], "depends_on": [],
         "acceptance": [{"id": "A1", "given": "existing booking", "when": "overlap",
                         "then": "reject", "kind": "automated"}]}]}


def checks():
    return {"version": 1, "checks": [{"id": "C1", "story_id": "S1", "kind": "acceptance",
            "criteria": ["A1"], "command": ["{python}", "{qa}/check.py"],
            "expected_tests": ["reject_overlap"], "timeout_seconds": 5},
            {"id": "H1", "story_id": "S1", "kind": "health", "criteria": [],
             "command": ["{python}", "{qa}/health.py"], "expected_tests": ["environment"],
             "timeout_seconds": 5}]}


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        for path in ["workspace", "qa", ".storyloop"]:
            (self.project / path).mkdir()
        self.write("product.json", product())
        self.write("checks.json", {"version": 1, "checks": []})
        (self.project / "brief.md").write_text("Simple reservations", encoding="utf-8")

    def write(self, path, value):
        (self.project / path).write_text(json.dumps(value), encoding="utf-8")

    def respond(self, result):
        script = "import json; print(" + repr(json.dumps(result)) + ")"
        self.write("config.json", {"version": 1, "adapter": "command",
            "agent_command": [sys.executable, "-c", script], "max_attempts": 3,
            "max_seconds": 30, "agent_timeout_seconds": 3, "check_timeout_seconds": 3,
            "repeated_failure_limit": 2})

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("storyloop.discovery"),
                             "product discovery module must exist")
        return importlib.import_module("storyloop.discovery")

    def test_discussion_saves_valid_product_history_and_invalidates_approval(self):
        self.respond(product("Changed reservation"))
        self.write(".storyloop/approval.json", {"digest": "old"})
        updated = self.module().discuss(self.project, "Change objective")
        self.assertEqual(updated["goal"], "Changed reservation")
        self.assertEqual(json.loads((self.project / "product.json").read_text())["goal"],
                         "Changed reservation")
        self.assertFalse((self.project / ".storyloop/approval.json").exists())
        history = list((self.project / ".storyloop/history").glob("*.json"))
        self.assertTrue(history)
        self.assertIn("Change objective", history[0].read_text(encoding="utf-8"))

    def test_invalid_product_does_not_modify_existing_product(self):
        self.respond({"version": 1, "goal": ""})
        before = (self.project / "product.json").read_bytes()
        with self.assertRaises(Exception) as caught:
            self.module().discuss(self.project, "change")
        self.assertNotIsInstance(caught.exception, AssertionError)
        self.assertEqual((self.project / "product.json").read_bytes(), before)

    def test_prepare_persists_qa_and_new_scaffold_without_executing(self):
        self.respond({"checks": checks(), "qa_files": [{"path": "check.py", "content":
            "raise RuntimeError('must not execute during prepare')"}],
            "workspace_files": [{"path": "app.py", "content": "def reserve(): pass\n"}]})
        self.module().prepare(self.project)
        self.assertTrue((self.project / "qa/check.py").exists())
        self.assertTrue((self.project / "workspace/app.py").exists())
        self.assertEqual(json.loads((self.project / "checks.json").read_text()), checks())

    def test_prepare_rejects_traversal_and_overwrite_before_writing(self):
        (self.project / "workspace/app.py").write_text("existing", encoding="utf-8")
        for target in ["../escape.py", "/escape.py", "C:\\escape.py", "app.py"]:
            with self.subTest(path=target):
                self.respond({"checks": checks(), "qa_files": [{"path": "check.py", "content": "x"}],
                              "workspace_files": [{"path": target, "content": "new"}]})
                with self.assertRaises(Exception) as caught:
                    self.module().prepare(self.project)
                self.assertNotIsInstance(caught.exception, AssertionError)
                self.assertFalse((self.project / "qa/check.py").exists())
                self.assertEqual((self.project / "workspace/app.py").read_text(), "existing")


    def test_file_ancestor_collisions_are_rejected_without_partial_writes(self):
        self.respond({"checks": checks(), "qa_files": [{"path": "a", "content": "first"},
                     {"path": "a/b.py", "content": "second"}], "workspace_files": []})
        with self.assertRaises(Exception):
            self.module().prepare(self.project)
        self.assertFalse((self.project / "qa/a").exists())


if __name__ == "__main__":
    unittest.main()
