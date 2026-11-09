"""L1 intervention events and conservative legacy inference."""

from pathlib import Path
import unittest

from storyloop.intervention_events import summarize_interventions
from storyloop.metrics_collect import collect


class InterventionEventTests(unittest.TestCase):
    def test_events_distinguish_routine_correction_and_automatic_repair(self):
        record = {"change_id": "C-1", "intervention_events": [
            {"kind": "contract_approval", "at": "2026-01-01T00:00:00+00:00",
             "stop": "DRAFT", "change_id": "C-1", "source": "approve"},
            {"kind": "acceptance_repair", "at": "2026-01-01T00:01:00+00:00",
             "stop": "ORACLE_DEFECT", "change_id": "C-1", "source": "engine", "automatic": True},
            {"kind": "overrule", "at": "2026-01-01T00:02:00+00:00",
             "stop": "ORACLE_DEFECT", "change_id": "C-1", "source": "run"},
        ], "started_at": "2026-01-01T00:00:00+00:00", "ended_at": "2026-01-01T00:03:00+00:00",
                  "ledger": [{"wall_ms": 100, "cost_usd": 0.2}]}
        row = summarize_interventions([record])["by_change"]["C-1"]
        self.assertEqual((row["routine"], row["nonroutine"], row["unknown"]), (1, 1, 0))
        self.assertEqual(row["automatic_repair"], 1)
        self.assertEqual(row["agent_ms"], 100)
        self.assertEqual(row["wall_ms"], 180000)
        self.assertEqual(row["cost_coverage"], 1.0)

    def test_legacy_dogfood_samples_are_inferred_without_guessing(self):
        root = Path(__file__).parent / "fixtures/intervention_dogfood"
        rows = summarize_interventions(collect(root)["changes"])["by_change"]
        self.assertEqual(rows["FIX-20261107-repair-next-path"]["nonroutine"], 0)
        self.assertEqual(rows["FIX-20261107-repair-next-path"]["routine"], 2)
        self.assertEqual(rows["FIX-20261107-scratch-glob"]["nonroutine"], 3)
        self.assertEqual(rows["FIX-20261107-scratch-glob"]["source"], "inferred")
        self.assertEqual(set(rows["FIX-20261107-scratch-glob"]["nonroutine_types"]),
                         {"replan", "acceptance_repair", "overrule"})

    def test_missing_events_or_legacy_evidence_is_unknown(self):
        row = summarize_interventions([{"change_id": "C-2", "intervention_events": None,
                                        "repairs": None, "overrules": None,
                                        "planning_history": None}])["by_change"]["C-2"]
        self.assertIsNone(row["nonroutine"])
        self.assertEqual(row["unknown"], 1)


if __name__ == "__main__":
    unittest.main()
