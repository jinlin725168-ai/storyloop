"""Behavioral tests for metric calibration rules and deferred signals."""

from __future__ import annotations

from copy import deepcopy
import math
import unittest

from storyloop.metrics_rules import (
    DEFAULTS,
    MANUAL_DEFERRED,
    TEXTS,
    deferred_signals,
    suggest,
    thresholds,
)
from storyloop.models import StoryLoopError


def metric(value: float, n: int, changes: list[str] | None = None) -> dict:
    return {"value": value, "n": n, "changes": changes or [f"C-{i}" for i in range(n)]}


def metrics_for(rule: str, value: float, n: int) -> dict:
    changes = [f"C-{i}" for i in range(n)]
    if rule == "R1":
        return {"M1b": {"budget_exhausted": round(value * n), "budget_denominator": n,
                         "n": 999, "progressing": 2, "progressing_denominator": 3,
                         "progressing_value": 0.6667, "changes": changes}}
    if rule == "R2":
        return {"M1": metric(value, n),
                "M3": {**metric(0.1, n, changes), "attribution": {"implement": 7, "contract": 3}}}
    if rule == "R3":
        return {"M2": {"by": {"light": metric(value, n, changes)}}}
    if rule == "R4":
        return {"M3": {**metric(value, n, changes), "attribution": {}}}
    if rule == "R5":
        total = round(value * n)
        return {"M4": {"agent_failure_value": value, "agent_failure_denominator": n,
                        "agent_failure_total": total,
                        "agent_failures": {"codex": total // 2, "claude": total - total // 2},
                        "agent_failure_changes": changes}}
    if rule == "R6":
        return {"M5": {"by": {"light": metric(value, n, changes)}}}
    if rule == "R7":
        return {"M6": metric(value, n, changes)}
    if rule == "R8":
        return {"M7": {**metric(value, n, changes), "failure_changes": changes[:round(value * n)]}}
    if rule == "R9":
        return {"M8": metric(value, n, changes)}
    if rule == "R10":
        return {"M9": {"by": {"light": metric(value, n, changes)}}}
    if rule == "R11":
        return {"M11": {"n": n, "calls": n, "cost_coverage": 1.0, "changes": changes,
                         "by": {"review|strong": {"calls": n // 2, "cost_usd": value,
                                                    "cost_coverage": 1.0},
                                "implement|standard": {"calls": n - n // 2,
                                                        "cost_usd": 1 - value,
                                                        "cost_coverage": 1.0}}}}
    if rule == "R12":
        return {"M12": {"recurrence_rate": metric(value, n, changes),
                         "unclaimed_rate": metric(0.0, n, [])}}
    raise AssertionError(rule)


class ThresholdTests(unittest.TestCase):
    def test_defaults_match_the_approved_rule_table(self):
        self.assertEqual(DEFAULTS, {
            "R1": {"threshold": 0.2, "min_sample": 10},
            "R2": {"threshold": 0.4, "min_sample": 10},
            "R3": {"threshold": 0.2, "min_sample": 10},
            "R4": {"threshold": 0.15, "min_sample": 10},
            "R5": {"threshold": 0.5, "min_sample": 5},
            "R6": {"threshold": 0.1, "min_sample": 10},
            "R7": {"threshold": 0.05, "min_sample": 10},
            "R8": {"threshold": 0.5, "low": 0.2, "min_sample": 5},
            "R9": {"threshold": 0.3, "min_sample": 10},
            "R10": {"threshold": 1.5, "min_sample": 10},
            "R11": {"threshold": 0.5, "min_sample": 5},
            "R12": {"threshold": 0.3, "min_sample": 10},
        })

    def test_every_rule_accepts_configured_threshold_and_minimum_sample(self):
        overrides = {rule: {"threshold": index / 100, "min_sample": index}
                     for index, rule in enumerate(DEFAULTS, start=1)}
        overrides["R8"]["low"] = 0.04

        result = thresholds({"metrics": {"thresholds": overrides}})

        for index, rule in enumerate(DEFAULTS, start=1):
            with self.subTest(rule=rule):
                self.assertEqual(result[rule]["threshold"], index / 100)
                self.assertEqual(result[rule]["min_sample"], index)
        self.assertEqual(result["R8"]["low"], 0.04)
        self.assertEqual(DEFAULTS["R1"]["threshold"], 0.2)

    def test_invalid_overrides_name_the_bad_key(self):
        cases = (
            ({"R99": {"threshold": 1}}, "R99"),
            ({"R9": {"threshold": "high"}}, "R9.threshold"),
            ({"R9": {"threshold": True}}, "R9.threshold"),
            ({"R9": {"threshold": math.inf}}, "R9.threshold"),
            ({"R9": {"threshold": -0.1}}, "R9.threshold"),
            ({"R8": {"low": math.nan}}, "R8.low"),
            ({"R9": {"min_sample": True}}, "R9.min_sample"),
            ({"R9": {"min_sample": 1.5}}, "R9.min_sample"),
            ({"R9": {"min_sample": -1}}, "R9.min_sample"),
            ({"R9": {"other": 1}}, "R9.other"),
        )
        for override, key in cases:
            with self.subTest(override=override), self.assertRaisesRegex(StoryLoopError, key):
                thresholds({"metrics": {"thresholds": override}})


class SuggestionTests(unittest.TestCase):
    BOUNDARIES = {rule: values["threshold"] for rule, values in DEFAULTS.items()}

    def test_every_rule_does_not_fire_exactly_at_its_threshold(self):
        for rule, boundary in self.BOUNDARIES.items():
            with self.subTest(rule=rule):
                output = suggest(metrics_for(rule, boundary, DEFAULTS[rule]["min_sample"]), DEFAULTS)
                self.assertFalse(any(item.get("rule") == rule and "text" in item for item in output))

        output = suggest(metrics_for("R8", DEFAULTS["R8"]["low"], 5), DEFAULTS)
        self.assertFalse(any(item.get("rule") == "R8" and "text" in item for item in output))
        m12 = metrics_for("R12", 0.0, 10)
        m12["M12"]["unclaimed_rate"] = metric(0.7, 10)
        self.assertFalse(any("text" in item for item in suggest(m12, DEFAULTS)))

    def test_every_rule_reports_its_minimum_sample_boundary(self):
        firing = {rule: values["threshold"] + 0.1 for rule, values in DEFAULTS.items()}
        firing["R2"] = 0.1
        firing["R8"] = 0.7
        for rule, value in firing.items():
            n = DEFAULTS[rule]["min_sample"] - 1
            with self.subTest(rule=rule):
                items = suggest(metrics_for(rule, value, n), DEFAULTS)
                row = next(item for item in items if item["rule"] == rule)
                self.assertEqual(row["insufficient"], f"{n}/{DEFAULTS[rule]['min_sample']}")

    def test_every_rule_fires_past_its_boundary(self):
        firing = {rule: values["threshold"] + 0.1 for rule, values in DEFAULTS.items()}
        firing["R2"] = 0.3
        firing["R8"] = 0.7
        for rule, value in firing.items():
            with self.subTest(rule=rule):
                items = suggest(metrics_for(rule, value, DEFAULTS[rule]["min_sample"]), DEFAULTS)
                self.assertTrue(any(item.get("rule") == rule and "text" in item for item in items))

    def test_fixed_secondary_conditions_observe_strict_boundaries(self):
        r2 = metrics_for("R2", 0.3, 10)
        r2["M3"]["attribution"] = {"implement": 6, "contract": 4}
        self.assertFalse(any("text" in item for item in suggest(r2, DEFAULTS)))

        r5 = metrics_for("R5", 0.1, 10)
        r5["M4"]["agent_failures"] = {"codex": 7, "claude": 3}
        self.assertFalse(any("text" in item for item in suggest(r5, DEFAULTS)))
        r5["M4"]["agent_failures"] = {"codex": 8, "claude": 2}
        self.assertTrue(any(item.get("rule") == "R5" and "text" in item
                            for item in suggest(r5, DEFAULTS)))

    def test_r1_uses_budget_denominator_and_requires_recorded_progress(self):
        m = metrics_for("R1", 0.3, 10)
        m["M1b"]["n"] = 1
        self.assertTrue(any(item.get("rule") == "R1" and "text" in item
                            for item in suggest(m, DEFAULTS)))
        m["M1b"]["progressing_value"] = None
        m["M1b"]["progressing_denominator"] = 0
        row = next(item for item in suggest(m, DEFAULTS) if item["rule"] == "R1")
        self.assertEqual(row["unavailable"], "PROGRESS_EVIDENCE_NOT_RECORDED")

    def test_r5_does_not_treat_unknown_adapter_as_concentration(self):
        m = metrics_for("R5", 0.1, 10)
        m["M4"]["agent_failures"] = {"未记录": 9, "codex": 1}
        self.assertFalse(any("text" in item for item in suggest(m, DEFAULTS)))

    def test_r8_low_branch_and_r12_unclaimed_submetric_fire(self):
        r8 = suggest(metrics_for("R8", 0.1, 5), DEFAULTS)
        self.assertEqual(next(item for item in r8 if "text" in item)["threshold"], 0.2)

        m12 = metrics_for("R12", 0.0, 10)
        m12["M12"]["unclaimed_rate"] = metric(0.8, 10)
        row = next(item for item in suggest(m12, DEFAULTS) if "text" in item)
        self.assertEqual((row["rule"], row["metric"], row["threshold"]),
                         ("R12", "unclaimed_rate", 0.7))

    def test_r11_uses_known_cost_calls_as_sample(self):
        m = metrics_for("R11", 0.8, 10)
        m["M11"]["cost_coverage"] = 0.4
        for row in m["M11"]["by"].values():
            row["cost_coverage"] = 0.4
        result = suggest(m, DEFAULTS)
        self.assertEqual(next(item for item in result if item["rule"] == "R11")["insufficient"],
                         "4/5")

    def test_evidence_is_capped_and_texts_contain_no_numbers(self):
        m = metrics_for("R9", 0.75, 12)
        row = next(item for item in suggest(m, DEFAULTS) if "text" in item)
        self.assertEqual((len(row["evidence"]), row["more"]), (10, 2))
        self.assertFalse(any(character.isdigit() for text in TEXTS.values() for character in text))


class DeferredSignalTests(unittest.TestCase):
    def test_supersede_chain_longer_than_two_links_is_reported(self):
        records = [
            {"change_id": "A", "superseded_by": "B"},
            {"change_id": "B", "supersedes": "A", "superseded_by": "C"},
            {"change_id": "C", "supersedes": "B", "superseded_by": "D"},
            {"change_id": "D", "supersedes": "C"},
        ]
        signal = next(item for item in deferred_signals(records)
                      if item["item"] == "变更链执行语义")
        self.assertTrue(signal["triggered"])
        self.assertEqual(signal["evidence"], ["A", "B", "C", "D"])

    def test_smoke_stagnation_needs_ten_real_passed_snapshots(self):
        records = [{"change_id": f"C-{index}", "status": "PASSED", "signal_evidence": {
            "layers": {"smoke": {"results": [{"id": "S"}]}}
        }} for index in range(10)]
        signal = next(item for item in deferred_signals(records)
                      if item["item"] == "顺利完成的变更也沉淀系统冒烟")
        self.assertTrue(signal["triggered"])
        self.assertEqual(len(signal["evidence"]), 10)

        missing = deepcopy(records)
        missing[-1]["signal_evidence"] = {}
        signal = next(item for item in deferred_signals(missing)
                      if item["item"] == "顺利完成的变更也沉淀系统冒烟")
        self.assertFalse(signal["triggered"])
        self.assertEqual(signal["unavailable"], "SMOKE_HISTORY_NOT_RECORDED")

    def test_unstored_historical_signals_are_explicitly_unavailable(self):
        records = [{"change_id": "A", "signal_evidence": {
            "stories": [{"id": "S-1"}, {"id": "S-2"}],
            "layers": {}, "traceability": [],
        }}]
        signals = {item["item"]: item for item in deferred_signals(records)}
        for item in ("故事级检查点", "非确定性判据重复执行"):
            with self.subTest(item=item):
                self.assertFalse(signals[item]["triggered"])
                self.assertEqual(signals[item]["evidence"], [])
                self.assertEqual(signals[item]["unavailable"],
                                 "HISTORICAL_EVIDENCE_NOT_RECORDED")

    def test_manual_deferred_list_is_fixed_and_nonempty(self):
        self.assertEqual(MANUAL_DEFERRED, [
            "升级性质与黄金快照对拍", "隐式行为检测与技术栈 profile", "安全模块",
            "协作出口", "perf/docs/port 性质", "知识库补充", "跨仓库的经验晋升",
        ])


if __name__ == "__main__":
    unittest.main()
