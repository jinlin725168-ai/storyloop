"""Preserved behavior of linked base stops before legacy path coverage grows."""

from pathlib import Path
import tempfile
import unittest

from storyloop.project_loop import LoopServices, load_run_state, run_change
from tests.test_baseline_behavior import git
from tests.test_failure_routing import approved
from tests.test_project_loop import smoke_payload


class BoundaryLegacyFlowCharacterization(unittest.TestCase):
    def test_prior_feature_survives_collision_and_current_feature_is_not_legacy(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            change_id = approved(project, add_core=True, add_pom=True)
            prior_path = project / "modules/orders/service.py"
            current_path = project / "modules/orders/current.py"
            stash_before = git(project, "stash", "list")

            def implement(root, context):
                if context["attempt"] == 1:
                    prior_path.parent.mkdir(parents=True, exist_ok=True)
                    prior_path.write_text("VALUE = 2\n", encoding="utf-8")
                else:
                    current_path.write_text("VALUE = 3\n", encoding="utf-8")
                    (root / "pom.xml").write_text("<project>new</project>\n", encoding="utf-8")
                return {"summary": "candidate", "questions": []}

            result = run_change(project, change_id, LoopServices(
                generate_smoke=lambda root, state: smoke_payload(change_id),
                implement=implement,
                review=lambda root, context: {"verdict": "pass", "summary": "ok",
                                              "findings": [], "questions": []},
            ))
            saved = load_run_state(project, change_id)

            self.assertEqual(result["status"], "BASE_CHANGE_REQUIRED")
            self.assertIn("modules/orders/service.py", result["legacy_paths"])
            self.assertIn("modules/orders/service.py", saved["legacy_paths"])
            self.assertNotIn("modules/orders/current.py", result["legacy_paths"])
            self.assertEqual(prior_path.read_text(encoding="utf-8"), "VALUE = 2\n")
            self.assertFalse(current_path.exists())
            self.assertEqual((project / "pom.xml").read_text(encoding="utf-8"),
                             "<project>old</project>\n")
            self.assertEqual(git(project, "stash", "list"), stash_before)
            self.assertIn(f"--change {saved['blocked_by']}", result["next"])
            self.assertLess(result["next"].index("storyloop approve"),
                            result["next"].index("storyloop run"))
            self.assertLess(result["next"].index("先 storyloop run"),
                            result["next"].index("取回或丢弃暂存"))


if __name__ == "__main__":
    unittest.main()
