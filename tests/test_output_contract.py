"""Machine-readable output contracts bind new observable fields to approved ACs."""

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
from tests.test_baseline_behavior import repository
from tests.test_entry_feasibility import contract


def _new_output_product() -> dict:
    value = contract("cli", "storyloop change <project> --json")
    value["stories"][0]["acceptance"][0]["new_output"] = True
    return value


def _output_contract() -> dict:
    return {"location": "VE-ENTRY: change JSON", "fields": [
        {"name": "inherited_constraints", "type": "array<object>",
         "meaning": "沿用的已批准约束"},
        {"name": "inherited_constraints[].id", "type": "string",
         "meaning": "约束的稳定编号"}]}


class OutputContractTests(unittest.TestCase):
    def test_new_draft_missing_contract_blocks_approval_and_change_json_names_ac(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            draft = create_change(root, "idea", _new_output_product(),
                                  require_output_contract=True)
            result = draft_result(root, draft)
            blocker = next(item for item in result["blocking_items"]
                           if item.get("criterion_id") == "AC-1")
            self.assertIn("output_contract", blocker["text"])
            self.assertRegex(blocker["id"], r"^CHK\d+$")
            self.assertIn(f"{len(result['blocking_items'])} 个阻断项", result["next"])
            with self.assertRaisesRegex(StoryLoopError, "AC-1"):
                approve_change(root, draft["change_id"])

    def test_location_field_name_type_and_meaning_are_required(self):
        value = _new_output_product()
        criterion = value["stories"][0]["acceptance"][0]
        samples = [
            ({"fields": _output_contract()["fields"]}, "location"),
            ({"location": "VE-ENTRY", "fields": [{"type": "string", "meaning": "value"}]}, "name"),
            ({"location": "VE-ENTRY", "fields": [{"name": "value", "meaning": "value"}]}, "type"),
            ({"location": "VE-ENTRY", "fields": [{"name": "value", "type": "string"}]}, "meaning"),
        ]
        for output, missing in samples:
            with self.subTest(missing=missing):
                criterion["output_contract"] = output
                errors = blocking_items(evaluate_checklist(value, require_output_contract=True))
                self.assertTrue(any(item.get("criterion_id") == "AC-1" and missing in item["text"]
                                    for item in errors), errors)

    def test_valid_contract_round_trips_and_old_change_remains_compatible(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            value = _new_output_product()
            value["stories"][0]["acceptance"][0]["output_contract"] = _output_contract()
            draft = create_change(root, "idea", value, require_output_contract=True)
            self.assertEqual(load_change(root, draft["change_id"])["product"]["stories"][0][
                "acceptance"][0]["output_contract"], _output_contract())
            self.assertFalse([item for item in draft_result(root, draft)["blocking_items"]
                              if item.get("criterion_id") == "AC-1"])
            with patch("storyloop.approvals._intake_approval", return_value={}):
                self.assertEqual(approve_change(root, draft["change_id"])["status"], "APPROVED")
            legacy = create_change(root, "old", _new_output_product())
            self.assertNotIn("output_contract_required", load_change(root, legacy["change_id"]))
            self.assertFalse(blocking_items(evaluate_checklist(
                legacy["product"], require_output_contract=False)))

    def test_unmarked_criterion_does_not_require_output_contract(self):
        value = contract("cli", "storyloop change <project> --json")
        errors = blocking_items(evaluate_checklist(value, require_output_contract=True))
        self.assertFalse(any(item.get("criterion_id") == "AC-1" for item in errors))

    def test_blockers_are_independent_per_criterion(self):
        value = _new_output_product()
        first = value["stories"][0]["acceptance"][0]
        first["output_contract"] = _output_contract()
        value["stories"][0]["acceptance"].append({**first, "id": "AC-2",
                                                     "output_contract": {"location": "VE-ENTRY"}})
        errors = blocking_items(evaluate_checklist(value, require_output_contract=True))
        self.assertEqual({item["criterion_id"] for item in errors if "criterion_id" in item},
                         {"AC-2"})

    def test_new_output_flag_is_only_for_automated_criteria(self):
        from storyloop.product_contract import validate_product

        value = _new_output_product()
        value["stories"][0]["acceptance"][0]["kind"] = "manual"
        with self.assertRaisesRegex(StoryLoopError, "new_output"):
            validate_product(value)

    def test_object_and_object_list_need_named_child_fields(self):
        value = _new_output_product()
        criterion = value["stories"][0]["acceptance"][0]
        for kind, child in (("object", "payload.id"), ("array<object>", "payload[].id")):
            with self.subTest(kind=kind):
                output = {"location": "VE-ENTRY: JSON", "fields": [
                    {"name": "payload", "type": kind, "meaning": "结果"}]}
                criterion["output_contract"] = output
                errors = blocking_items(evaluate_checklist(value, require_output_contract=True))
                self.assertTrue(any("child" in item["text"] for item in errors), errors)
                output["fields"].append({"name": child, "type": "string", "meaning": "标识"})
                errors = blocking_items(evaluate_checklist(value, require_output_contract=True))
                self.assertFalse([item for item in errors if item.get("criterion_id") == "AC-1"])

    def test_role_schema_and_skill_guidance_expose_contract(self):
        from storyloop.role_schemas import CRITERION

        self.assertIn("new_output", CRITERION["properties"])
        self.assertIn("output_contract", CRITERION["properties"])
        skills = Path(__file__).resolve().parents[1] / "storyloop/skills"
        for name in ("spec-craft", "oracle-craft", "review"):
            with self.subTest(skill=name):
                self.assertIn("output_contract", (skills / name / "SKILL.md").read_text(encoding="utf-8"))

    def test_cli_new_change_sets_gate_and_exposes_missing_contract(self):
        from storyloop.cli import dispatch, parser
        from storyloop.readiness import approve_readiness, inspect_readiness
        from tests.routing_support import readiness_repository
        from tests.test_intake import assessed_product

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            readiness_repository(root)
            ready = inspect_readiness(root)
            approve_readiness(root, ready["digest"], "Local tests")
            value = assessed_product()
            value["stories"][0]["verification_entries"] = contract()["stories"][0][
                "verification_entries"]
            for criterion in value["stories"][0]["acceptance"]:
                if criterion["kind"] == "automated":
                    criterion["entry_ids"] = ["VE-ENTRY"]
                    criterion["observation"] = "Read the public command result"
            value["stories"][0]["acceptance"][0]["new_output"] = True
            args = parser().parse_args(["change", str(root), "--idea", "new output", "--json",
                                        "--no-interactive"])
            with patch("storyloop.cli.invoke_project_agent", return_value=value):
                result = dispatch(args)
            self.assertTrue(result["output_contract_required"])
            self.assertTrue(any(item.get("criterion_id") == "AC-1" and "output_contract" in item["text"]
                                for item in result["blocking_items"]))
