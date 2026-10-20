"""Twelve real engine changes, with only the model replaced by subprocess scripts."""
from pathlib import Path
import tempfile
import unittest

from storyloop.metrics import build_metrics
from storyloop.metrics_render import render_metrics
from storyloop.project_loop import load_run_state
from storyloop.storage import load_json
from storyloop.telemetry import ledger_entries
from tests.metrics_e2e_support import run_scenarios


class MetricsEndToEndTests(unittest.TestCase):
    def test_twelve_changes_preserve_real_evidence_and_explain_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root, changes, ci_folder = run_scenarios(Path(directory), self)
            result = build_metrics(root, None, ci_folder)
            metrics = result["metrics"]
            self.assertEqual(result["changes"], 12)
            self.assertIsNotNone(metrics["M1"]["value"])
            self.assertEqual(metrics["M5"]["changes"], [changes["first"]])
            self.assertEqual(metrics["M6"]["numerator"], 1)
            self.assertEqual(metrics["M6"]["denominator"], 2)
            self.assertEqual(metrics["M1b"]["budget_exhausted"], 1)
            self.assertEqual(metrics["M2"]["numerator"], 1)
            self.assertIn(changes["spec"], metrics["M3"]["changes"])
            self.assertEqual(metrics["M4"]["numerator"], 1)
            self.assertEqual(metrics["M4"]["agent_failure_total"], 1)
            self.assertEqual(metrics["M7"]["denominator"], 1)
            self.assertEqual(metrics["M8"]["numerator"], 1)
            self.assertIsNotNone(metrics["M9"]["value"])
            self.assertEqual(metrics["M10"]["value"]["supersede"], 1)
            self.assertEqual(metrics["M10"]["value"]["overrules"], 1)
            self.assertEqual(metrics["M10"]["value"]["continue_heavy"], 1)
            self.assertGreater(metrics["M11"]["calls"], 0)
            self.assertGreater(metrics["M12"]["recurrence_rate"]["numerator"], 0)
            self.assert_unclaimed_matches_ledger(root, changes, metrics["M12"]["unclaimed_rate"])
            self.assertTrue(any(s.get("rule") and s.get("text")
                                for s in result["suggestions"]))
            self.assertEqual(result["discrepancies"], [])
            self.assertTrue(render_metrics(result).rstrip().endswith("以上建议由人决定是否采纳"))
            self.assert_real_evidence(root, changes)

    def assert_unclaimed_matches_ledger(self, root, changes, actual):
        provided_count = unclaimed_count = 0
        unclaimed_changes = set()
        for identifier in changes.values():
            for entry in ledger_entries(root, identifier):
                provided = entry.get("lessons_provided", [])
                claimed = entry.get("lessons_claimed", [])
                provided_count += len(provided)
                unused = [lesson for lesson in provided if lesson not in claimed]
                unclaimed_count += len(unused)
                if unused:
                    unclaimed_changes.add(identifier)
        self.assertGreater(provided_count, 0)
        self.assertEqual(actual["numerator"], unclaimed_count)
        self.assertEqual(actual["denominator"], provided_count)
        self.assertEqual(actual["n"], provided_count)
        self.assertEqual(actual["value"], round(unclaimed_count / provided_count, 4))
        self.assertEqual(set(actual["changes"]), unclaimed_changes)
        self.assertEqual(actual["missing"], [])

    def assert_real_evidence(self, root, changes):
        for name in ("first", "second", "fix"):
            state = load_run_state(root, changes[name])
            self.assertEqual(state["status"], "PASSED")
            self.assertEqual(state["attempt"], 1)
            self.assertEqual(state["failures"], [])
        multi = load_run_state(root, changes["multi"])
        self.assertGreater(len(multi["failures"]), 0)
        self.assertGreater(multi["attempt"], 1)
        entries = ledger_entries(root, changes["multi"])
        self.assertTrue(any("L-0001" in row.get("lessons_provided", []) for row in entries))
        lessons = load_json(root / ".storyloop/state/lessons.json")
        self.assertIn(changes["multi"], lessons["L-0001"]["recurrences"])
        for identifier in changes.values():
            state = load_run_state(root, identifier)
            self.assertTrue(state["contract_commit"])
            self.assertTrue(ledger_entries(root, identifier))
