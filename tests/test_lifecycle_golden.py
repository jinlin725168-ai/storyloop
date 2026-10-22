"""Frozen public outputs reject status, verdict, budget and evidence drift."""
import json
from pathlib import Path
import unittest

from tests.lifecycle_support import capture_case, normalize_case

FIXTURES = Path(__file__).parent / "fixtures" / "lifecycle"


class GoldenNormalizerTests(unittest.TestCase):
    def test_normalization_keeps_distinct_digests_and_shared_references(self):
        value = {"a": "a" * 64, "b": "b" * 64, "again": "a" * 64,
                 "path": "/case/report.json", "exitcode": 1, "executed": 9}
        result = normalize_case(value, Path("/case"))
        self.assertEqual(result["a"], result["again"])
        self.assertNotEqual(result["a"], result["b"])
        self.assertEqual(result["path"], "<PROJECT>/report.json")
        self.assertEqual((result["exitcode"], result["executed"]), (1, 9))

    def test_changed_verdict_counts_budget_wording_and_binding_stay_visible(self):
        original = {"status": "PASSED", "exitcode": 0, "executed": 5, "attempt": 2,
                    "next": "原文", "sha256": "a" * 64, "reference": "a" * 64}
        for key, value in (("status", "VERIFIED"), ("exitcode", 2), ("executed", 4),
                           ("attempt", 1), ("next", "changed"), ("reference", "b" * 64)):
            changed = {**original, key: value}
            with self.subTest(key=key):
                self.assertNotEqual(normalize_case(original, Path("/case")),
                                    normalize_case(changed, Path("/case")))

    def test_only_paths_clocks_and_measured_durations_are_normalized(self):
        value = {"at": "2026-10-22T10:20:30+00:00", "loop_ms": 87,
                 "attempt": 3, "failures": ["second", "first"], "counts": {"executed": None}}
        expected = {"at": "<TIME>", "loop_ms": "<DURATION>", "attempt": 3,
                    "failures": ["second", "first"], "counts": {"executed": None}}
        self.assertEqual(normalize_case(value, Path("/case")), expected)

    def test_cli_call_identifiers_and_runner_durations_are_metadata(self):
        value = {"log": "review OK 0.2s\n记录: calls/20261022T090251-review-8d097b",
                 "runner": "Ran 7 tests in 0.031s\nFAILED (failures=1)"}
        expected = {"log": "review OK <DURATION>s\n记录: calls/<CALL-1>",
                    "runner": "Ran 7 tests in <DURATION>s\nFAILED (failures=1)"}
        self.assertEqual(normalize_case(value, Path("/case")), expected)

    def test_daily_change_ids_keep_type_and_reference_identity(self):
        before = {"id": "FEAT-20261022-001", "link": "FEAT-20261022-001",
                  "base": "BASE-20261022-001"}
        later = {"id": "FEAT-20261023-001", "link": "FEAT-20261023-001",
                 "base": "BASE-20261023-001"}
        normalized = normalize_case(before, Path("/case"))
        self.assertEqual(normalized, normalize_case(later, Path("/case")))
        self.assertNotEqual(normalized["id"], normalized["base"])


class LifecycleGoldenTests(unittest.TestCase):
    def test_reports_run_states_cli_and_git_match_the_pre_refactor_main(self):
        for name in ("success", "manual", "boundary", "repair", "supersede", "routed", "cost"):
            with self.subTest(case=name):
                expected = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
                self.assertEqual(capture_case(name), expected)
