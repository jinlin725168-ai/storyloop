"""Change natures: the contract says what kind of change it is, and the oracle follows from it."""

from __future__ import annotations

from copy import deepcopy
import unittest

from storyloop.models import (StoryLoopError, criterion_expects_red, criterion_tier, nature_of,
                              validate_product)
from storyloop.role_schemas import SCHEMAS, normalize, schema_errors
from tests.support import product

ALL_ROWS = ["positive", "degradation", "unknown_state", "form_parity",
            "regression", "state_preservation", "non_goal"]


def fix_product() -> dict:
    value = product()
    value["nature"] = "fix"
    value["defect"] = {
        "observed": "导出少一行", "expected": "导出全部记录",
        "reproduction": ["准备 3 条订单", "导出"],
        "reporter_hypothesis": "分页从 1 开始",
        "root_cause": [{"statement": "终止条件少算一行", "location": "modules/orders/service.py:1"}],
        "confirmed": ["终止条件少算一行"], "unconfirmed": [],
    }
    criterion = value["stories"][0]["acceptance"][0]
    criterion.update({"row_type": "positive", "reproduces_defect": True})
    value["coverage_exemptions"] = [{"row_type": row, "reason": "不适用"} for row in ALL_ROWS[1:]]
    return value


def refactor_product(subtype: str = "structure") -> dict:
    value = product()
    value["nature"] = "refactor"
    value["refactor"] = {"subtype": subtype, "scope": ["modules/orders/**"],
                         "invariants": ["对外行为不变"], "structural_goals": ["service.py 不再引用 legacy"]}
    if subtype == "dependency-swap":
        value["refactor"]["migration_map"] = [{"from": "legacy.get", "to": "httpx.get",
                                               "assertion": "grep legacy 计数为 0"}]
    return value


class ContractShapeTests(unittest.TestCase):
    def test_contracts_without_a_nature_are_features_and_still_valid(self):
        legacy = product()
        validate_product(legacy)
        self.assertEqual(nature_of(legacy), "feature")

    def test_each_nature_requires_its_own_block(self):
        validate_product(fix_product())
        validate_product(refactor_product())
        validate_product(refactor_product("dependency-swap"))
        missing = fix_product(); del missing["defect"]
        with self.assertRaises(StoryLoopError):
            validate_product(missing)
        stray = product(); stray["defect"] = fix_product()["defect"]
        with self.assertRaises(StoryLoopError):
            validate_product(stray)
        swap = refactor_product("dependency-swap"); del swap["refactor"]["migration_map"]
        with self.assertRaises(StoryLoopError):
            validate_product(swap)
        goalless = refactor_product(); goalless["refactor"]["structural_goals"] = []
        with self.assertRaises(StoryLoopError):
            validate_product(goalless)

    def test_root_causes_are_one_to_five_path_line_locations(self):
        bad = fix_product(); bad["defect"]["root_cause"][0]["location"] = "modules/orders/service.py"
        with self.assertRaises(StoryLoopError):
            validate_product(bad)
        many = fix_product(); many["defect"]["root_cause"] = many["defect"]["root_cause"] * 6
        with self.assertRaises(StoryLoopError):
            validate_product(many)

    def test_criterion_fields_and_consumer_references(self):
        value = product()
        value["consumers"] = [{"id": "C1", "consumer": "导出按钮", "trigger": "点击", "path": "web → api",
                               "in_scope": True, "verification": "接口用例"}]
        value["stories"][0]["acceptance"][0].update({"row_type": "regression", "oracle_class": "auto-heavy",
                                                     "consumers": ["C1"]})
        validate_product(value)
        unknown = deepcopy(value); unknown["stories"][0]["acceptance"][0]["consumers"] = ["C9"]
        with self.assertRaises(StoryLoopError):
            validate_product(unknown)
        mismatch = deepcopy(value); mismatch["stories"][0]["acceptance"][0]["oracle_class"] = "manual"
        with self.assertRaises(StoryLoopError):
            validate_product(mismatch)  # kind is automated, class says manual
        green_repro = deepcopy(value); green_repro["stories"][0]["acceptance"][0]["reproduces_defect"] = True
        with self.assertRaises(StoryLoopError):
            validate_product(green_repro)  # a reproduction must be a must-be-red row

    def test_row_types_decide_the_expected_state_and_classes_decide_the_tier(self):
        self.assertTrue(criterion_expects_red({"kind": "automated"}))
        self.assertTrue(criterion_expects_red({"kind": "automated", "row_type": "unknown_state"}))
        self.assertFalse(criterion_expects_red({"kind": "automated", "row_type": "state_preservation"}))
        self.assertEqual(criterion_tier({"kind": "automated"}), "fast")
        self.assertEqual(criterion_tier({"kind": "automated", "oracle_class": "auto-heavy"}), "heavy")
        self.assertEqual(criterion_tier({"kind": "manual"}), "manual")


class RoleSchemaTests(unittest.TestCase):
    def test_product_schema_accepts_the_new_blocks_and_nulls(self):
        answer = fix_product()
        answer["refactor"] = None
        answer["stories"][0]["acceptance"][0]["oracle_class"] = None
        body = normalize(answer, SCHEMAS["product"])
        self.assertEqual(schema_errors(body, SCHEMAS["product"]), [])
        validate_product(body)

    def test_acceptance_manifest_tests_may_carry_a_tier(self):
        answer = {"files": [{"path": "t.py", "content": "x"}],
                  "manifest": {"version": 1, "tests": [{"id": "T1", "criteria": ["AC-1"], "command": ["x"],
                                                        "timeout_seconds": 5, "tier": "heavy"}]}}
        self.assertEqual(schema_errors(answer, SCHEMAS["acceptance"]), [])
        answer["manifest"]["tests"][0]["tier"] = "slow"
        self.assertTrue(schema_errors(answer, SCHEMAS["acceptance"]))


if __name__ == "__main__":
    unittest.main()
