"""Base drafts have a locked, story-free contract and explicit build policy."""

from pathlib import Path
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.base_upgrade import propose_base_change
from storyloop.baseline import bootstrap_project
from storyloop.changes import contract_digest, load_change
from storyloop.models import StoryLoopError
from storyloop.role_schemas import schema_for
from tests.test_baseline_behavior import repository


class BaseContractTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        repository(self.root)

    def test_explicit_empty_build_is_accepted_and_approved(self):
        bootstrap_project(self.root, {"test_commands": [], "base_build_commands": []})
        result = propose_base_change(self.root, ["pom.xml"], "更新构建配置")
        change = load_change(self.root, result["change_id"])
        self.assertEqual(change["product"]["nature"], "base")
        self.assertEqual(change["product"]["stories"], [])
        self.assertEqual(change["product"]["base"]["scope"], "foundation")
        self.assertEqual(change["product"]["base"]["must_stay_green"],
                         ["full_regression", "system_smoke", "build"])
        approved = approve_change(self.root, result["change_id"])
        self.assertEqual(approved["approval"]["contract_digest"],
                         contract_digest(change["product"]))
        self.assertEqual(approved["approval"]["tier"], "heavy")

    def test_missing_build_key_rejects_approval(self):
        bootstrap_project(self.root, {"test_commands": []})
        result = propose_base_change(self.root, ["pom.xml"], "更新构建配置")
        with self.assertRaisesRegex(StoryLoopError, "base_build_commands"):
            approve_change(self.root, result["change_id"])

    def test_product_agent_cannot_propose_base(self):
        schema = schema_for("product", {})
        self.assertNotIn("base", schema["properties"]["nature"]["enum"])


if __name__ == "__main__":
    unittest.main()
