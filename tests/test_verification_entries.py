"""Frozen verification entries bind contract, oracle, and selected design path."""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.models import StoryLoopError
from storyloop.product_contract import validate_product
from storyloop.requirement_checklist import evaluate_checklist
from storyloop.smoke import _validate_manifest
from storyloop.role_schemas import schema_errors, schema_for
from tests.support import product
from tests.test_routing_planning import answer


def new_product() -> dict:
    value = product()
    first = value["stories"][0]
    first["verification_entries"] = [{"id": "VE-CLI", "kind": "cli",
                                      "locator": "storyloop run <project>",
                                      "observable": "status and next in JSON",
                                      "reason": "public command"}]
    second = deepcopy(first)
    second["id"] = "ST-2"
    second["depends_on"] = ["ST-1"]
    second["acceptance"][0]["id"] = "AC-2"
    second["verification_entries"][0].update(id="VE-HTTP", kind="http",
                                              locator="POST /api/orders")
    value["stories"].append(second)
    return value


def manifest() -> dict:
    return {"version": 1, "tests": [
        {"id": "T-1", "criteria": ["AC-1"], "entry_ids": ["VE-CLI"],
         "command": ["{python}", "-V"], "timeout_seconds": 10},
        {"id": "T-2", "criteria": ["AC-2"], "entry_ids": ["VE-HTTP"],
         "command": ["{python}", "-V"], "timeout_seconds": 10}]}


class ContractEntryTests(unittest.TestCase):
    def test_new_change_cannot_be_approved_without_entries(self):
        from storyloop.approvals import approve_change
        from storyloop.baseline import bootstrap_project
        from storyloop.changes import create_change
        from tests.test_baseline_behavior import repository

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            now = lambda: datetime(2026, 11, 8, tzinfo=timezone.utc)
            state = create_change(root, "idea", product(), now=now,
                                  require_verification_entries=True)
            with self.assertRaisesRegex(StoryLoopError, "verification_entries"):
                approve_change(root, state["change_id"], now=now)

    def test_new_contract_requires_each_story_entry_before_approval(self):
        value = new_product()
        self.assertIs(validate_product(value), value)
        del value["stories"][1]["verification_entries"]
        with self.assertRaises(StoryLoopError):
            validate_product(value)
        self.assertTrue(any(item["level"] == "error" and "verification" in item["text"]
                            for item in evaluate_checklist(value, require_entries=True)))

    def test_entry_ids_unique_and_fields_valid(self):
        for change in ("duplicate", "empty", "kind"):
            with self.subTest(change=change):
                value = new_product()
                entry = value["stories"][1]["verification_entries"][0]
                if change == "duplicate":
                    entry["id"] = "VE-CLI"
                elif change == "empty":
                    entry["locator"] = " "
                else:
                    entry["kind"] = "internal_mock"
                with self.assertRaises(StoryLoopError):
                    validate_product(value)

    def test_legacy_contract_and_manifest_remain_valid(self):
        value = product()
        self.assertIs(validate_product(value), value)
        old = manifest()
        old["tests"] = [{key: item for key, item in old["tests"][0].items()
                         if key != "entry_ids"}]
        self.assertEqual(_validate_manifest(old, {"product": value}), old)


class ManifestEntryTests(unittest.TestCase):
    def test_role_schemas_accept_entry_fields(self):
        self.assertEqual(schema_errors(new_product(), schema_for("product", {})), [])
        self.assertEqual(schema_errors({"files": [], "manifest": manifest()},
                                       schema_for("acceptance", {})), [])
        plan = answer()
        plan["options"][0]["entry_paths"] = [{"entry_id": "VE-CLI",
            "criterion_ids": ["AC-1"], "path": "app/service.py", "via": "CLI to service"}]
        self.assertEqual(schema_errors(plan, schema_for("design", {"mode": "routing-plan"})), [])

    def test_manifest_must_reference_own_story_entry(self):
        value = new_product()
        good = manifest()
        self.assertEqual(_validate_manifest(good, {"product": value}), good)
        for change in ("missing", "unknown", "cross_story"):
            with self.subTest(change=change):
                bad = deepcopy(good)
                if change == "missing":
                    del bad["tests"][0]["entry_ids"]
                else:
                    bad["tests"][0]["entry_ids"] = ["VE-NOPE" if change == "unknown" else "VE-HTTP"]
                with self.assertRaises(StoryLoopError):
                    _validate_manifest(bad, {"product": value})


class PlanEntryTests(unittest.TestCase):
    def test_selected_option_covers_automatic_entry_pairs_and_scope(self):
        from storyloop.verification_entries import validate_entry_paths

        value = new_product()
        option = {"touched": ["storyloop/cli.py", "app/api.py"], "creates": [],
                  "entry_paths": [
                      {"entry_id": "VE-CLI", "criterion_ids": ["AC-1"],
                       "path": "storyloop/cli.py", "via": "CLI dispatch to run"},
                      {"entry_id": "VE-HTTP", "criterion_ids": ["AC-2"],
                       "path": "app/api.py", "via": "HTTP controller to service"}]}
        self.assertIsNone(validate_entry_paths(option, value, manifest()))
        for change in ("missing", "outside", "cross_story"):
            with self.subTest(change=change):
                bad = deepcopy(option)
                if change == "missing":
                    bad["entry_paths"].pop()
                elif change == "outside":
                    bad["entry_paths"][1]["path"] = "app/other.py"
                else:
                    bad["entry_paths"][1]["criterion_ids"] = ["AC-1"]
                with self.assertRaises(StoryLoopError):
                    validate_entry_paths(bad, value, manifest())
        self.assertIsNone(validate_entry_paths({"touched": [], "creates": []}, product(),
                                                {"version": 1, "tests": []}))


class PlanningEntryIntegrationTests(unittest.TestCase):
    def setUp(self):
        from tests.test_routing_planning import PlanningTests
        PlanningTests.setUp(self)

    def ensure(self):
        from tests.test_routing_planning import PlanningTests
        return PlanningTests.ensure(self)

    def _new_contract(self):
        self.change["product"]["stories"][0]["verification_entries"] = [
            {"id": "VE-CLI", "kind": "cli", "locator": "app run", "observable": "result",
             "reason": "public command"}]

    def test_selected_option_requires_entry_paths_and_scoped_file(self):
        self._new_contract()
        suite = {"version": 1, "tests": [manifest()["tests"][0]]}
        with patch("storyloop.smoke.load_smoke_manifest", return_value=suite):
            with self.assertRaises(StoryLoopError):
                from storyloop.planning import validate_plan
                validate_plan(answer(), self.root, self.change, self.state)
            good = answer()
            good["options"][0]["entry_paths"] = [{"entry_id": "VE-CLI",
                "criterion_ids": ["AC-1"], "path": "app/service.py", "via": "CLI to service"}]
            self.assertEqual(validate_plan(good, self.root, self.change, self.state)["status"], "DONE")
            good["options"][0]["entry_paths"][0]["path"] = "app/other.py"
            with self.assertRaises(StoryLoopError):
                validate_plan(good, self.root, self.change, self.state)

    def test_light_new_contract_without_seam_path_asks_for_design(self):
        self._new_contract()
        self.assertEqual(self.ensure()["status"], "NEEDS_INPUT")


class ThreeWayEntryTests(unittest.TestCase):
    def test_gate_routes_mismatched_manifest_before_role_review(self):
        from storyloop.consistency import ensure_consistency

        change = {"change_id": "FEAT-entries", "product": new_product()}
        option = {"id": "main", "touched": ["app/api.py", "storyloop/cli.py"],
                  "creates": [], "dependencies": [], "entry_paths": []}
        state = {"routing": {"effective_tier": "heavy"},
                 "planning": {"plan": {"id": "plan", "main_option": "main",
                                       "options": [option]}}}
        bad = manifest()
        bad["tests"][0]["entry_ids"] = ["VE-NOPE"]
        with patch("storyloop.consistency.is_routed", return_value=True), \
             patch("storyloop.consistency.current_binding", return_value={}), \
             patch("storyloop.consistency._valid_design", return_value=True), \
             patch("storyloop.dependency_checks.verify_dependencies", return_value=[]), \
             patch("storyloop.consistency.load_smoke_manifest", return_value=bad), \
             patch("storyloop.consistency._review") as review, \
             patch("storyloop.consistency._outcome", side_effect=lambda p, s, r: r):
            record = ensure_consistency(Path("/tmp"), change, state, object())
        self.assertEqual(record["status"], "ORACLE_DEFECT")
        review.assert_not_called()

    def test_review_receives_three_way_evidence(self):
        from storyloop.consistency import _review

        value = new_product()
        change = {"change_id": "FEAT-entries", "product": value}
        option = {"id": "main", "entry_paths": [{"entry_id": "VE-CLI",
                  "criterion_ids": ["AC-1"], "path": "storyloop/cli.py", "via": "CLI to run"}]}
        state = {"planning": {"plan": {"main_option": "main", "options": [option],
                                    "id": "plan"}, "dependency_evidence": []}}
        seen = []
        with patch("storyloop.consistency.load_smoke_manifest", return_value=manifest()), \
             patch("storyloop.consistency.load_principles", return_value={}), \
             patch("storyloop.consistency.load_answers", return_value=[]), \
             patch("storyloop.consistency.suite_relative", return_value="acceptance/changes/FEAT-entries"), \
             patch("storyloop.consistency.answer_with_repair",
                   side_effect=lambda call, request, validate: seen.append(request) or
                   {"verdict": "coherent", "summary": "agrees", "questions": [], "findings": []}):
            _review(Path("/tmp"), change, state, object(), "plan.json")
        evidence = seen[0]["verification"]
        self.assertEqual(evidence["contract_entries"][0]["entries"][0]["id"], "VE-CLI")
        self.assertEqual(evidence["manifest_tests"][0]["entry_ids"], ["VE-CLI"])
        self.assertEqual(evidence["entry_paths"], option["entry_paths"])

    def test_deterministic_findings_route_contract_oracle_and_design(self):
        from storyloop.consistency import verification_findings, consistency_status

        value = new_product()
        change = {"change_id": "FEAT-entries", "product": value}
        suite = manifest()
        option = {"id": "main", "touched": ["app/api.py", "storyloop/cli.py"],
                  "creates": [], "entry_paths": [
                      {"entry_id": "VE-CLI", "criterion_ids": ["AC-1"],
                       "path": "storyloop/cli.py", "via": "CLI to run"},
                      {"entry_id": "VE-HTTP", "criterion_ids": ["AC-2"],
                       "path": "app/api.py", "via": "HTTP to service"}]}
        self.assertEqual(verification_findings(change, suite, option), [])
        cases = [
            (lambda c, m, o: c["product"]["stories"][1].pop("verification_entries"), "NEEDS_SPEC_FIX"),
            (lambda c, m, o: m["tests"][0].update(entry_ids=["VE-NOPE"]), "ORACLE_DEFECT"),
            (lambda c, m, o: o["entry_paths"].pop(), "DESIGN_REVISION_REQUIRED"),
        ]
        for mutate, expected in cases:
            with self.subTest(expected=expected):
                copy_change, copy_suite, copy_option = deepcopy(change), deepcopy(suite), deepcopy(option)
                mutate(copy_change, copy_suite, copy_option)
                findings = verification_findings(copy_change, copy_suite, copy_option)
                self.assertEqual(consistency_status(findings), expected)
                self.assertTrue(findings[0]["blocking"])
        self.assertEqual(verification_findings({"change_id": "FEAT-old", "product": product()},
                                               {"version": 1, "tests": []}, {}), [])


if __name__ == "__main__":
    unittest.main()
