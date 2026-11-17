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

    def test_nonroutine_interventions_are_counted_by_stop_class(self):
        # 2026-11-17 B4: L3 #478 had 4 non-routine interventions, all recoveries from engine or
        # workspace stops; the accounting had to separate them from contract stops by hand.
        record = {"change_id": "C-2", "intervention_events": [
            {"kind": "reset", "at": "2026-01-01T00:00:00+00:00",
             "stop": "WORKSPACE_TAINTED", "change_id": "C-2", "source": "reset"},
            {"kind": "replan", "at": "2026-01-01T00:01:00+00:00",
             "stop": "CHARACTERIZATION_FAILED", "change_id": "C-2", "source": "run"},
            {"kind": "contract_approval", "at": "2026-01-01T00:02:00+00:00",
             "stop": "DRAFT", "change_id": "C-2", "source": "approve"},
        ], "started_at": "2026-01-01T00:00:00+00:00", "ended_at": "2026-01-01T00:03:00+00:00",
                  "ledger": []}
        summary = summarize_interventions([record])
        self.assertEqual(summary["by_change"]["C-2"]["nonroutine_by_class"],
                         {"infrastructure": 1, "oracle": 1})
        self.assertEqual(summary["summary"]["nonroutine_by_class"], {"infrastructure": 1, "oracle": 1})

    def test_legacy_dogfood_samples_are_inferred_without_guessing(self):
        root = Path(__file__).parent / "fixtures/intervention_dogfood"
        rows = summarize_interventions(collect(root)["changes"])["by_change"]
        self.assertEqual(rows["FIX-20261107-repair-next-path"]["nonroutine"], 0)
        self.assertEqual(rows["FIX-20261107-repair-next-path"]["routine"], 2)
        self.assertEqual(rows["FIX-20261107-scratch-glob"]["nonroutine"], 3)
        self.assertEqual(rows["FIX-20261107-scratch-glob"]["source"], "inferred")
        self.assertEqual(set(rows["FIX-20261107-scratch-glob"]["nonroutine_types"]),
                         {"replan", "acceptance_repair", "overrule"})

    def test_engine_replanning_history_is_not_a_human_replan(self):
        # 2026-11-15 self-iteration D: an L3 scope re-plan had no observed event, so its
        # history entry was inferred as a human replan on top of the repeated design approval.
        event = {"kind": "design_approval", "at": "2026-01-01T00:00:00+00:00",
                 "stop": "DESIGN_APPROVAL_REQUIRED", "change_id": "C-3", "source": "design-approve"}
        record = {"change_id": "C-3", "status": "PASSED", "design_approved": True,
                  "intervention_events": [event, {**event, "repeated": True}],
                  "planning_history": [{"reason": "L3 scope or risk expansion", "attempt": 1},
                                       {"reason": "Fix the plan", "attempt": 1, "automatic": True},
                                       {"reason": "Human wording", "attempt": 1}]}
        row = summarize_interventions([record])["by_change"]["C-3"]
        self.assertEqual(row["nonroutine_types"], {"design_approval": 1, "replan": 1})

    def test_engine_invalidation_marks_history_automatic(self):
        from storyloop.routing_upgrade import invalidate_planning

        state = {"attempt": 1, "planning": {"plan": {}}}
        invalidate_planning(state, "Base upgrade changed plan inputs")
        self.assertIs(state["planning"]["history"][-1]["automatic"], True)

    def test_missing_events_or_legacy_evidence_is_unknown(self):
        row = summarize_interventions([{"change_id": "C-2", "intervention_events": None,
                                        "repairs": None, "overrules": None,
                                        "planning_history": None}])["by_change"]["C-2"]
        self.assertIsNone(row["nonroutine"])
        self.assertEqual(row["unknown"], 1)


if __name__ == "__main__":
    unittest.main()
