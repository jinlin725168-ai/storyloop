"""Behavioral tests for cross-change metric computation."""

from __future__ import annotations

from pathlib import Path
import unittest

from storyloop.metrics_compute import (
    attempts,
    contract_ratio,
    escapes,
    first_pass,
    forced_heavy,
    interventions,
    metric,
    overrule_rate,
    rework,
    stuck,
    upgrades,
)
from tests.lesson_support import _git, cleanup_repo, commit_all, git_repo, head


def rec(change_id: str, **overrides: object) -> dict:
    """Return the complete normalized shape produced by metrics_collect."""
    value = {
        "change_id": change_id,
        "nature": "feature",
        "origin_change": None,
        "status": "PASSED",
        "failures": [],
        "overrules": [],
        "attempts": 1,
        "tier_initial": "standard",
        "tier_effective": "standard",
        "routing_history": [],
        "stops": [],
        "agent_failures": [],
        "answers": 0,
        "resets": 0,
        "repairs": 0,
        "manual_acceptance": 0,
        "continue_heavy": False,
        "design_approved": False,
        "by_role": {},
        "ledger": [],
        "review_stop_findings": 0,
        "commits": {
            "source_head": None,
            "contract_commit": None,
            "implementation_commit": None,
            "merge_commit": None,
        },
        "gaps": {},
    }
    value.update(overrides)
    return value


def _commit_lines(project: Path, relative: str, count: int, message: str) -> str:
    target = project / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(f"line {index}\n" for index in range(count)), encoding="utf-8")
    return commit_all(project, message)


def git_project_with_two_changes(
    *, contract_lines: tuple[int, int], implementation_lines: tuple[int, int],
) -> tuple[Path, list[dict]]:
    project = git_repo()
    records = []
    for index, (contract_count, implementation_count) in enumerate(
        zip(contract_lines, implementation_lines, strict=True), start=1,
    ):
        source = head(project)
        contract = _commit_lines(project, f"contracts/{index}.txt", contract_count,
                                 f"contract {index}")
        implementation = _commit_lines(project, f"src/{index}.txt", implementation_count,
                                       f"implementation {index}")
        records.append(rec(
            f"C{index}", tier_effective="light" if index == 1 else "standard",
            commits={"source_head": source, "contract_commit": contract,
                     "implementation_commit": implementation, "merge_commit": None},
        ))
    return project, records


class ComputeTests(unittest.TestCase):
    def test_metric_rounds_and_zero_denominator_has_no_sample(self):
        self.assertEqual(metric("MX", 2, 3, ["A"])["value"], 0.6667)

        empty = metric("MX", 0, 0, [])

        self.assertIsNone(empty["value"])
        self.assertEqual((empty["n"], empty["reason"]), (0, "NO_SAMPLE"))

    def test_first_pass_uses_passed_with_known_failures_as_denominator(self):
        records = [
            rec("A", status="PASSED"),
            rec("B", status="PASSED", failures=[{}]),
            rec("C", status="NO_PROGRESS"),
            rec("D", status="PASSED", failures=None,
                gaps={"failures": "UNREADABLE"}),
        ]

        result = first_pass(records)

        self.assertEqual((result["numerator"], result["denominator"], result["value"]),
                         (1, 2, 0.5))
        self.assertEqual(result["changes"], ["A"])
        self.assertEqual(result["missing"], [{"change_id": "D", "reason": "UNREADABLE"}])

    def test_zero_first_pass_denominator_is_null_with_reason(self):
        result = first_pass([rec("A", status="NO_PROGRESS")])

        self.assertIsNone(result["value"])
        self.assertEqual(result["reason"], "NO_SAMPLE")

    def test_attempts_separates_mean_sample_from_full_budget_population(self):
        records = [
            rec("A", status="BUDGET_EXHAUSTED", stops=["BUDGET_EXHAUSTED"], attempts=4,
                failures=[{"signature": "old"}, {"signature": "new"}]),
            rec("B", attempts=2),
            rec("C", attempts=None, gaps={"attempts": "UNREADABLE"}),
        ]

        result = attempts(records)

        self.assertEqual((result["value"], result["numerator"], result["denominator"]),
                         (3.0, 6, 2))
        self.assertEqual((result["n"], result["budget_denominator"]), (2, 3))
        self.assertEqual((result["budget_exhausted"], result["progressing"],
                          result["progressing_denominator"]), (1, 1, 1))
        self.assertEqual(result["changes"], ["A"])
        self.assertEqual(result["attempt_changes"], ["A", "B"])

    def test_budget_progress_requires_two_recorded_signatures(self):
        result = attempts([
            rec("A", status="BUDGET_EXHAUSTED", failures=[{"signature": "one"}]),
            rec("B", status="BUDGET_EXHAUSTED",
                failures=[{"signature": "same"}, {"signature": "same"}]),
        ])

        self.assertEqual((result["budget_exhausted"], result["progressing"],
                          result["progressing_denominator"]), (2, 0, 1))
        self.assertIn({"change_id": "A", "reason": "UNREADABLE"}, result["missing"])

    def test_upgrade_only_counts_in_run_increases(self):
        history = [
            {"attempt": 0, "from_tier": "light", "to_tier": "standard"},
            {"attempt": 2, "from_tier": "standard", "to_tier": "heavy"},
        ]

        result = upgrades([
            rec("A", tier_initial="light", routing_history=history),
            rec("B", tier_initial="light", routing_history=[]),
        ])

        self.assertEqual(result["value"], 0.5)
        self.assertEqual(result["changes"], ["A"])
        self.assertEqual(result["by"]["light"]["value"], 0.5)

    def test_no_routing_is_missing_not_zero(self):
        result = upgrades([rec("OLD", tier_initial=None, routing_history=None)])

        self.assertEqual((result["denominator"], result["value"]), (0, None))
        self.assertEqual(result["missing"], [{"change_id": "OLD", "reason": "NO_ROUTING"}])

    def test_superseded_change_counts_as_rework_not_as_a_pass(self):
        records = [
            rec("A", status="SUPERSEDED", stops=["NEEDS_SPEC_FIX"], superseded_by="B"),
            rec("B", status="PASSED", supersedes="A"),
        ]

        self.assertEqual(rework(records)["numerator"], 1)
        self.assertEqual(rework(records)["changes"], ["A"])
        self.assertEqual(first_pass(records)["denominator"], 1)

    def test_rework_counts_each_failure_attribution(self):
        result = rework([
            rec("A", failures=[{"attribution": "contract"},
                                {"attribution": "implement"}]),
            rec("B", failures=[{"attribution": "implement"}]),
        ])

        self.assertEqual(result["attribution"], {"implement": 2, "contract": 1})
        self.assertEqual((result["numerator"], result["denominator"]), (1, 2))

    def test_stuck_preserves_unrecorded_adapter_instead_of_guessing_from_role(self):
        result = stuck([
            rec("A", status="NO_PROGRESS", stops=["NO_PROGRESS"],
                agent_failures=[{"adapter": "codex", "role": "implement"},
                                {"role": "review"}]),
            rec("B"),
        ])

        self.assertEqual((result["no_progress"], result["agent_failure_total"]), (1, 2))
        self.assertEqual(result["agent_failures"], {"codex": 1, "未记录": 1})
        self.assertEqual(result["agent_failure_changes"], ["A"])

    def test_escape_needs_origin_change_and_lists_only_escaped_changes(self):
        records = [
            rec("A", status="PASSED", tier_effective="light"),
            rec("B", status="PASSED", tier_effective="light"),
            rec("F", status="PASSED", nature="fix", origin_change="A"),
            rec("G", status="PASSED", nature="fix", origin_change=None),
        ]

        result = escapes(records)

        self.assertEqual((result["numerator"], result["denominator"], result["changes"]),
                         (1, 4, ["A"]))
        self.assertEqual(result["by"]["light"]["value"], 0.5)

    def test_forced_heavy_reports_outcomes_attempts_and_actual_implement_tier(self):
        records = [
            rec("A", continue_heavy=True, attempts=2,
                by_role={"implement": {"tiers": ["strong"]}}),
            rec("B", continue_heavy=True, status="BUDGET_EXHAUSTED", attempts=4,
                ledger=[{"role": "implement", "tier_applied": "standard"}]),
            rec("C", continue_heavy=False),
        ]

        result = forced_heavy(records)

        self.assertEqual(result["outcomes"], {"PASSED": 1, "BUDGET_EXHAUSTED": 1})
        self.assertEqual((result["value"], result["mean_attempts"], result["n"]),
                         (0.5, 3.0, 2))
        self.assertEqual(list(result["by"]), ["standard", "strong"])
        self.assertEqual(result["by"]["strong"]["outcomes"], {"PASSED": 1})

    def test_overrule_rate_counts_historical_overruled_findings_in_denominator(self):
        result = overrule_rate([
            rec("A", overrules=[{"findings": ["one", "two"]}], review_stop_findings=1),
            rec("B", overrules=[], review_stop_findings=1),
        ])

        self.assertEqual((result["numerator"], result["denominator"], result["value"]),
                         (1, 4, 0.25))
        self.assertEqual(result["changes"], ["A"])

    def test_contract_ratio_median_from_git_and_tier_partition(self):
        project, records = git_project_with_two_changes(
            contract_lines=(10, 30), implementation_lines=(20, 20),
        )
        self.addCleanup(cleanup_repo, project)

        result = contract_ratio(project, records)

        self.assertEqual((result["value"], result["n"]), (1.0, 2))
        self.assertEqual(result["by"]["light"]["value"], 0.5)
        self.assertEqual(result["by"]["standard"]["value"], 1.5)

    def test_contract_ratio_rejects_missing_wrong_order_empty_and_binary_evidence(self):
        project = git_repo()
        self.addCleanup(cleanup_repo, project)
        source = head(project)
        contract = _commit_lines(project, "contract.md", 2, "contract")
        implementation = _commit_lines(project, "app.py", 4, "implementation")
        _git(project, "commit", "--allow-empty", "-q", "-m", "empty implementation")
        empty_implementation = head(project)
        (project / "asset.bin").write_bytes(b"\x00binary")
        binary_implementation = commit_all(project, "binary implementation")
        records = [
            rec("MISSING", commits={"source_head": source, "contract_commit": contract,
                                    "implementation_commit": None}),
            rec("ORDER", commits={"source_head": implementation, "contract_commit": source,
                                  "implementation_commit": contract}),
            rec("EMPTY", commits={"source_head": contract, "contract_commit": implementation,
                                  "implementation_commit": empty_implementation}),
            rec("BINARY", commits={"source_head": empty_implementation,
                                   "contract_commit": empty_implementation,
                                   "implementation_commit": binary_implementation}),
        ]

        result = contract_ratio(project, records)

        self.assertIsNone(result["value"])
        self.assertEqual(result["n"], 0)
        self.assertEqual(result["missing"], [
            {"change_id": "MISSING", "reason": "UNREADABLE"},
            {"change_id": "ORDER", "reason": "UNREADABLE"},
            {"change_id": "EMPTY", "reason": "UNREADABLE"},
            {"change_id": "BINARY", "reason": "UNREADABLE"},
        ])

    def test_interventions_are_sorted_and_missing_is_not_counted_as_zero(self):
        result = interventions([
            rec("A", answers=3, resets=1, design_approved=True),
            rec("B", answers=1, overrules=[{}], continue_heavy=True,
                supersedes="OLD", manual_acceptance=2),
            rec("C", answers=None, gaps={"answers": "UNREADABLE"}),
        ])

        self.assertEqual(list(result["value"]), [
            "answers", "manual_acceptance", "resets", "overrules", "supersede",
            "design_approval", "continue_heavy",
        ])
        self.assertEqual(result["value"]["answers"], 4)
        self.assertIn({"change_id": "C", "reason": "UNREADABLE"}, result["missing"])


if __name__ == "__main__":
    unittest.main()
