"""Contract entry feasibility and per-criterion draft blockers."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from storyloop.changes import create_change, load_change
from storyloop.models import StoryLoopError
from storyloop.product_cli import draft_result
from storyloop.requirement_checklist import blocking_items, evaluate_checklist
from tests.support import product
from tests.test_baseline_behavior import repository
from tests.test_baseline_behavior import git as test_git
from tests.test_intake import assessed_product


def contract(kind="public_function", locator="storyloop.cli.parser()"):
    value = product()
    criterion = value["stories"][0]["acceptance"][0]
    criterion["entry_ids"] = ["VE-ENTRY"]
    criterion["observation"] = "在给定状态下调用入口并观察返回结果"
    value["stories"][0]["verification_entries"] = [{
        "id": "VE-ENTRY", "kind": kind, "locator": locator,
        "observable": "返回结果", "reason": "公共入口"}]
    return value


class EntryFeasibilityTests(unittest.TestCase):
    def test_missing_function_blocks_approval_and_keeps_draft(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            value = contract(locator="storyloop.missing_module.absent()")
            draft = create_change(root, "idea", value, require_verification_entries=True)
            with self.assertRaisesRegex(StoryLoopError, "VE-ENTRY"):
                approve_change(root, draft["change_id"])
            self.assertEqual(load_change(root, draft["change_id"])["status"], "DRAFT")

    def test_missing_symbol_and_missing_cli_command_block_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            for kind, locator in [
                ("public_function", "storyloop.cli.missing_symbol(project)"),
                ("cli", "storyloop no-such-command <project>"),
                ("stop_json", "storyloop no-such-command <project> --json 输出")]:
                with self.subTest(kind=kind):
                    draft = create_change(root, "idea", contract(kind, locator),
                                          require_verification_entries=True)
                    with self.assertRaisesRegex(StoryLoopError, "VE-ENTRY"):
                        approve_change(root, draft["change_id"])

    def test_existing_entry_and_legacy_contract_are_not_rejected_as_unresolvable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            for value in (contract("cli", "storyloop run <project>"), product()):
                with self.subTest(new="verification_entries" in value["stories"][0]):
                    draft = create_change(root, "idea", value)
                    try:
                        approve_change(root, draft["change_id"])
                    except StoryLoopError as exc:
                        self.assertNotIn("VE-ENTRY", str(exc))

    def test_function_resolves_from_committed_source_not_working_tree(self):
        from storyloop.verification_entries import validate_entry_feasibility

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / "sample.py").write_text("def public(value):\n    return value\n")
            test_git(root, "add", "sample.py")
            test_git(root, "commit", "-q", "-m", "public function")
            bootstrap_project(root)
            value = contract(locator="sample.public(value)")
            validate_entry_feasibility(root, value)
            (root / "sample.py").write_text("def missing(value):\n    return value\n")
            validate_entry_feasibility(root, value)

    def test_auxiliary_bench_command_is_in_actual_cli_registry(self):
        from storyloop.verification_entries import _cli_commands

        self.assertIn("bench", _cli_commands())

    def test_missing_observation_is_a_criterion_blocker_with_id_and_text(self):
        value = contract()
        del value["stories"][0]["acceptance"][0]["observation"]
        items = blocking_items(evaluate_checklist(value, require_observation=True))
        found = [item for item in items if item.get("criterion_id") == "AC-1"]
        self.assertEqual(len(found), 1)
        self.assertRegex(found[0]["id"], r"^CHK\d+$")
        self.assertIn("observation", found[0]["text"])

    def test_criterion_question_suppresses_only_its_observation_blocker(self):
        value = contract()
        value["stories"][0]["acceptance"].append({
            "id": "AC-2", "given": "state", "when": "action", "then": "result",
            "kind": "automated", "entry_ids": ["VE-ENTRY"]})
        del value["stories"][0]["acceptance"][0]["observation"]
        value["questions"] = [{"id": "Q-1", "text": "How can AC-1 be observed?",
                               "criterion_id": "AC-1", "options": ["A", "B"]}]
        items = blocking_items(evaluate_checklist(value, require_observation=True))
        self.assertFalse(any(item.get("criterion_id") == "AC-1" for item in items))
        self.assertTrue(any(item.get("criterion_id") == "AC-2" for item in items))

    def test_observation_round_trips_and_change_json_preserves_blocker_count(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            value = contract()
            draft = create_change(root, "idea", value, require_observation=True)
            output = draft_result(root, draft)
            self.assertEqual(output["product"]["stories"][0]["acceptance"][0]["observation"],
                             value["stories"][0]["acceptance"][0]["observation"])
            del value["stories"][0]["acceptance"][0]["observation"]
            draft = create_change(root, "idea", value, require_observation=True)
            output = draft_result(root, draft)
            items = [item for item in output["blocking_items"] if item.get("criterion_id") == "AC-1"]
            self.assertEqual(len(items), 1)
            self.assertIn("1", output["next"])

    def test_change_dispatch_json_includes_blocker_details(self):
        from storyloop.cli import dispatch, parser
        from storyloop.readiness import inspect_readiness, approve_readiness
        from tests.routing_support import readiness_repository

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            readiness_repository(root)
            ready = inspect_readiness(root)
            approve_readiness(root, ready["digest"], "Local tests")
            value = assessed_product()
            value["stories"][0]["verification_entries"] = contract()["stories"][0]["verification_entries"]
            for criterion in value["stories"][0]["acceptance"]:
                if criterion["kind"] == "automated":
                    criterion["entry_ids"] = ["VE-ENTRY"]
                    criterion["observation"] = "在给定状态调用入口并观察结果"
            del value["stories"][0]["acceptance"][0]["observation"]
            args = parser().parse_args(["change", str(root), "--idea", "new idea", "--json",
                                        "--no-interactive"])
            with patch("storyloop.cli.invoke_project_agent", return_value=value):
                result = dispatch(args)
            blocker = next(item for item in result["blocking_items"]
                           if item.get("criterion_id") == "AC-1")
            self.assertIn("observation", blocker["text"])
            self.assertIn("1", result["next"])

    def test_spec_craft_marks_new_observation_as_must_be_red(self):
        skill = (Path(__file__).resolve().parents[1] / "storyloop/skills/spec-craft/SKILL.md").read_text()
        self.assertIn("version: 1.4.3", skill)
        self.assertIn("observation", skill)
        self.assertIn("regression/state_preservation/non_goal", skill)


if __name__ == "__main__":
    unittest.main()
