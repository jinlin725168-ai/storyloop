"""Existing L1 counting and uncertainty before sharing classification with log."""

from __future__ import annotations

import unittest

from storyloop.intervention_events import summarize_interventions


class InterventionL1PreservationTests(unittest.TestCase):
    def test_observed_events_keep_existing_l1_counts(self):
        def event(kind: str, at: str, **extra):
            return {"kind": kind, "at": at, "change_id": "C-1",
                    "source": "test", "stop": "REVIEW", **extra}

        record = {"change_id": "C-1", "intervention_events": [
            event("contract_approval", "2026-01-01T00:00:00+00:00"),
            event("overrule", "2026-01-01T00:01:00+00:00"),
            event("acceptance_repair", "2026-01-01T00:02:00+00:00", automatic=True),
            event("replan", "2026-01-01T00:03:00+00:00", automatic=True),
        ]}
        row = summarize_interventions([record])["by_change"]["C-1"]
        self.assertEqual(row["routine"], 1)
        self.assertEqual(row["nonroutine"], 1)
        self.assertEqual(row["automatic_repair"], 1)
        self.assertEqual(row["unknown"], 0)
        self.assertEqual(row["nonroutine_types"].get("overrule"), 1)
        self.assertNotIn("replan", row["nonroutine_types"])

    def test_missing_run_keeps_nonroutine_unknown(self):
        record = {"change_id": "C-2", "run_present": False,
                  "intervention_events": None, "repair_entries": None,
                  "overrules": None, "planning_history": None}
        row = summarize_interventions([record])["by_change"]["C-2"]
        self.assertIsNone(row["nonroutine"])
        self.assertEqual(row["unknown"], 1)


if __name__ == "__main__":
    unittest.main()
