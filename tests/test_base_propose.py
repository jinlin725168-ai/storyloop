"""Planned base changes can be proposed directly, then accepted through base-update."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

from storyloop.base_upgrade import propose_base_change, upgrade_baseline
from storyloop.baseline import bootstrap_project, load_baseline
from storyloop.models import StoryLoopError
from storyloop.storage import load_json
from tests.test_baseline_behavior import git, repository


class BaseProposeTests(unittest.TestCase):
    def _project(self, root: Path) -> None:
        repository(root)
        (root / "core.py").write_text("STABLE = True\n", encoding="utf-8")
        git(root, "add", "core.py")
        git(root, "commit", "-q", "-m", "core")
        bootstrap_project(root, {"test_commands": [[sys.executable, "-c", "pass"]]})

    def test_manual_proposal_is_accepted_by_base_update(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._project(root)
            proposed = propose_base_change(root, ["core.py", "docs/base.md"], "tighten core")
            self.assertEqual(proposed["status"], "PROPOSED")
            proposal = load_json(Path(proposed["proposal"]))
            self.assertEqual(proposal["triggered_by"], "manual")
            self.assertEqual(proposal["reason"], "tighten core")
            self.assertEqual({item["path"] for item in proposal["changes"]}, {"core.py", "docs/base.md"})

            (root / "core.py").write_text("STABLE = False\n", encoding="utf-8")
            (root / "docs").mkdir()
            (root / "docs/base.md").write_text("base\n", encoding="utf-8")
            git(root, "add", "core.py", "docs/base.md")
            git(root, "commit", "-q", "-m", "base: tighten core")
            result = upgrade_baseline(root, proposed["change_id"])
            self.assertEqual(result["status"], "BASELINE_UPGRADED")
            self.assertEqual(load_baseline(root)["baseline_version"], "1.0.1")

    def test_upstream_sync_may_include_mutable_paths_when_allowed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._project(root)
            with self.assertRaises(StoryLoopError):
                propose_base_change(root, ["core.py", "modules/orders/service.py"], "upstream")
            proposed = propose_base_change(root, ["core.py", "modules/orders/service.py"], "upstream",
                                           allow_mutable=True)
            proposal = load_json(Path(proposed["proposal"]))
            categories = {item["path"]: item["category"] for item in proposal["changes"]}
            self.assertEqual(categories["modules/orders/service.py"], "UPSTREAM_MUTABLE")
            self.assertEqual(categories["core.py"], "FOUNDATION_CHANGE")

    def test_integration_path_inside_a_module_root_is_still_a_base_path(self):
        """A nested build manifest (monorepo pom/package.json) is an integration point, not feature code."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / "modules").mkdir()
            (root / "modules/pom.xml").write_text("<project/>\n", encoding="utf-8")
            git(root, "add", "modules/pom.xml")
            git(root, "commit", "-q", "-m", "modules pom")
            bootstrap_project(root, {"test_commands": [[sys.executable, "-c", "pass"]],
                                     "module_roots": ["modules"], "integration_paths": ["modules/pom.xml"]})
            proposed = propose_base_change(root, ["modules/pom.xml"], "add test dependency")
            self.assertEqual(proposed["status"], "PROPOSED")
            proposal = load_json(Path(proposed["proposal"]))
            self.assertEqual(proposal["changes"], [{"path": "modules/pom.xml", "category": "INTEGRATION_CHANGE"}])

    def test_rejects_ci_paths_mutable_paths_and_empty_input(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._project(root)
            with self.assertRaises(StoryLoopError):
                propose_base_change(root, [".github/workflows/ci.yml"], "ci")
            with self.assertRaises(StoryLoopError):
                propose_base_change(root, ["modules/orders/service.py"], "feature work")
            with self.assertRaises(StoryLoopError):
                propose_base_change(root, [], "nothing")
            with self.assertRaises(StoryLoopError):
                propose_base_change(root, ["core.py"], "")


if __name__ == "__main__":
    unittest.main()
