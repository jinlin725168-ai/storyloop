"""An approved plan owns updates to existing regression assertions."""

from pathlib import Path
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.changes import create_change
from storyloop.design_approval import approve_design, replan
from storyloop.onboarding_cli import bootstrap_with_project_checks
from storyloop.project_loop import LoopServices, load_run_state, run_change
from tests.routing_support import (_install_counted_smoke, local_retrospective_adapter,
                                   routed_acceptance, routed_characterize, routed_implement,
                                   routed_product, routing_repository)
from tests.test_baseline_behavior import git
from tests.test_routing_planning import answer
from tests.test_structured_output import passing_review


SNAPSHOT = "tests/fixtures/lifecycle/repair.json"


class RegressionUpdateTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        routing_repository(self.root)
        snapshot = self.root / SNAPSHOT
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text('{"value": 0}\n')
        (self.root / "tests/test_snapshot.py").write_text(
            "import json, unittest\nfrom pathlib import Path\n"
            "class Snapshot(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        source = Path('modules/orders/service.py')\n"
            "        value = 0 if not source.exists() else __import__('runpy').run_path(str(source))['VALUE']\n"
            "        expected = json.loads(Path('tests/fixtures/lifecycle/repair.json').read_text())['value']\n"
            "        self.assertEqual(value, expected)\n")
        git(self.root, "add", "tests")
        git(self.root, "commit", "-q", "-m", "old snapshot")
        _install_counted_smoke(self.root)
        bootstrap_with_project_checks(self.root, {
            "module_roots": ["app", "modules"], "test_roots": ["tests"],
            "unit_commands": [["{python}", "-m", "unittest", "discover", "-s", "tests"]],
            "check_commands": [["{python}", "-c", "pass"]]})
        local_retrospective_adapter(self.root)
        self.product = routed_product()
        change = create_change(self.root, "orders", self.product)
        self.change_id = change["change_id"]
        approve_change(self.root, self.change_id, tier="standard")
        self.seen_review = []
        self.seen_design = []

    def services(self, *, declared=False, edit_snapshot=False):
        def design(project, context):
            self.seen_design.append(context)
            plan = answer(self.product)
            if declared:
                plan["options"][0]["touched"].append(SNAPSHOT)
                plan["options"][0]["regression_updates"] = [{
                    "path": SNAPSHOT, "criterion_ids": ["AC-1"],
                    "reason": "AC-1 replaces the old value captured in the snapshot"}]
            return plan

        def implement(project, context):
            routed_implement(project, context)
            if edit_snapshot:
                (project / SNAPSHOT).write_text('{"value": 1}\n')
            return {"summary": "Implement AC-1", "questions": []}

        def review(project, context):
            self.seen_review.append(context)
            if context.get("mode") == "consistency":
                return passing_review(project, context)
            if context["layers"]["unit"]["status"] != "PASSED":
                return {"verdict": "fail", "summary": "Old snapshot conflicts with AC-1",
                        "questions": [], "findings": [{
                            "id": "OLD-SNAPSHOT", "criterion_id": "AC-1", "target": "implementation",
                            "perspective": "oracle-strength", "introduced": False,
                            "location": "tests/test_snapshot.py:8", "blocking": True,
                            "disposition": "MUST_FIX", "evidence": f"{SNAPSHOT}:1 still expects 0",
                            "message": "The old snapshot contradicts AC-1",
                            "required_change": "Update the approved regression snapshot"}]}
            return passing_review(project, context)

        return LoopServices(lambda p, c: routed_acceptance(self.change_id), implement, review,
                            characterize=routed_characterize, design=design)

    def test_unplanned_old_snapshot_conflict_stops_for_replan_with_evidence(self):
        result = run_change(self.root, self.change_id, self.services())
        self.assertEqual(result["status"], "DESIGN_REVISION_REQUIRED", result)
        self.assertIn(SNAPSHOT, str(result["stop"]))
        self.assertIn("tests/test_snapshot.py", str(result["stop"]))
        self.assertEqual(len(load_run_state(self.root, self.change_id)["failures"]), 1)

    def test_declared_snapshot_update_can_pass_and_reviewer_sees_diff(self):
        result = run_change(self.root, self.change_id, self.services(declared=True,
                                                                     edit_snapshot=True))
        if result["status"] == "DESIGN_APPROVAL_REQUIRED":
            approve_design(self.root, self.change_id, result["plan_id"])
            result = run_change(self.root, self.change_id,
                                self.services(declared=True, edit_snapshot=True))
        self.assertEqual(result["status"], "PASSED", result)
        reviews = [item for item in self.seen_review if "layers" in item]
        self.assertTrue(reviews)
        self.assertIn(SNAPSHOT, str(reviews[-1].get("regression_update_diffs")))
        self.assertEqual(reviews[-1]["design_plan"]["regression_updates"][0]["criterion_ids"],
                         ["AC-1"])

    def test_undeclared_edit_of_existing_snapshot_is_rejected(self):
        result = run_change(self.root, self.change_id, self.services(edit_snapshot=True))
        self.assertEqual(result["status"], "BOUNDARY_VIOLATION", result)
        self.assertIn("UNDECLARED_REGRESSION_UPDATE", str(result["violations"]))
        self.assertIn(SNAPSHOT, str(result["violations"]))

    def test_replan_can_add_the_conflicting_snapshot_and_finish(self):
        stopped = run_change(self.root, self.change_id, self.services())
        self.assertEqual(stopped["status"], "DESIGN_REVISION_REQUIRED")
        replan(self.root, self.change_id, "Declare the old snapshot for AC-1")
        result = run_change(self.root, self.change_id,
                            self.services(declared=True, edit_snapshot=True))
        if result["status"] == "DESIGN_APPROVAL_REQUIRED":
            approve_design(self.root, self.change_id, result["plan_id"])
            result = run_change(self.root, self.change_id,
                                self.services(declared=True, edit_snapshot=True))
        self.assertEqual(result["status"], "PASSED", result)
        self.assertIn(SNAPSHOT, str(self.seen_design[-1].get("regression_conflict")))


if __name__ == "__main__":
    unittest.main()
