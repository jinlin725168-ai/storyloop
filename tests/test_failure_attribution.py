"""Who has to fix a failed attempt, and whether the loop is still making progress (subproject 2a §3, §5)."""

import unittest

from storyloop.failure_attribution import (check_findings, failure_signature, finding_key, layer_attribution,
                                           next_mode, no_progress, route, location_file, contract_ids)

CHANGE = {"product": {"stories": [{"id": "ST-1", "acceptance": [{"id": "AC-1"}, {"id": "AC-2"}]}]}}
SUITE = "acceptance/changes/FEAT-1"


def check(findings, exists=lambda path: True, overruled=frozenset()):
    return check_findings(findings, CHANGE, [SUITE], exists, set(overruled))


class CheckFindingsTests(unittest.TestCase):
    def test_attribution_evidence_requires_positive_line_and_column_numbers(self):
        for suffix in ("", ":0", ":-1", ":2:0", ":2:-1", ":2:3:4"):
            with self.subTest(suffix=suffix):
                [item] = check([{"target": "oracle", "criterion_id": "AC-1",
                                 "location": f"{SUITE}/flow.py{suffix}"}])
                self.assertEqual(item["target"], "implementation")
                self.assertIn("location", item["attribution_note"])

    def test_valid_line_and_column_pass_only_the_file_to_exists(self):
        seen = []
        def exists(path):
            seen.append(path)
            return True
        [item] = check([{"target": "oracle", "criterion_id": "AC-1",
                         "location": f"{SUITE}/flow.py:2:3"}], exists=exists)
        self.assertEqual(item["target"], "oracle")
        self.assertEqual(seen, [f"{SUITE}/flow.py"])

    def test_contract_ids_include_stories_and_criteria(self):
        self.assertEqual(contract_ids(CHANGE), {"ST-1", "AC-1", "AC-2"})

    def test_unsafe_locations_do_not_reach_the_filesystem(self):
        for location in ("/etc/passwd:1", "../outside:2", "a/../b:3", "C:\\temp\\x:2",
                         "x\x00.py:2", "a\nb.py:3", ".", "x.py:bad"):
            with self.subTest(location=location):
                self.assertIsNone(location_file(location))
                [item] = check([{"target": "contract", "criterion_id": "AC-1", "location": location}],
                               exists=lambda _: self.fail("unsafe location reached exists"))
                self.assertEqual(item["target"], "implementation")

    def test_equivalent_paths_have_the_same_key(self):
        self.assertEqual(location_file("./a//b.py:2:4"), "a/b.py")
        self.assertEqual(location_file("a\\b.py:2"), "a/b.py")

    def test_adjacent_directory_is_not_an_oracle_root(self):
        [item] = check([{"target": "oracle", "criterion_id": "AC-1",
                         "location": f"{SUITE}-other/flow.py:3"}])
        self.assertEqual(item["target"], "implementation")

    def test_normalized_oracle_root_is_recognized(self):
        [item] = check_findings([{"target": "oracle", "criterion_id": "AC-1",
                                 "location": f"{SUITE}/flow.py:3"}], CHANGE,
                               [f"./{SUITE}/"], lambda _: True, set())
        self.assertEqual(item["target"], "oracle")

    def test_checking_does_not_modify_original_finding(self):
        original = {"message": "m", "target": "oracle"}
        check([original])
        self.assertEqual(original, {"message": "m", "target": "oracle"})

    def test_a_well_evidenced_oracle_finding_keeps_its_target(self):
        [item] = check([{"message": "m", "target": "oracle", "criterion_id": "AC-1",
                         "location": f"{SUITE}/flow.py:3"}])
        self.assertEqual(item["target"], "oracle")
        self.assertNotIn("attribution_note", item)

    def test_an_unknown_criterion_falls_back_to_implementation(self):
        [item] = check([{"message": "m", "target": "oracle", "criterion_id": "AC-9",
                         "location": f"{SUITE}/flow.py:3"}])
        self.assertEqual(item["target"], "implementation")
        self.assertIn("criterion_id", item["attribution_note"])

    def test_an_oracle_finding_outside_the_protected_tests_falls_back(self):
        [item] = check([{"message": "m", "target": "oracle", "criterion_id": "AC-1",
                         "location": "modules/orders/service.py:1"}])
        self.assertEqual(item["target"], "implementation")

    def test_a_missing_file_falls_back(self):
        [item] = check([{"message": "m", "target": "contract", "criterion_id": "AC-1",
                         "location": "nowhere.py:1"}], exists=lambda path: False)
        self.assertEqual(item["target"], "implementation")

    def test_a_contract_finding_may_point_at_code(self):
        [item] = check([{"message": "m", "target": "contract", "criterion_id": "ST-1",
                         "location": "modules/orders/service.py:1"}])
        self.assertEqual(item["target"], "contract")

    def test_an_overruled_finding_counts_as_implementation(self):
        finding = {"message": "m", "target": "oracle", "criterion_id": "AC-1", "location": f"{SUITE}/flow.py:3"}
        [item] = check([finding], overruled={finding_key(finding)})
        self.assertEqual(item["target"], "implementation")
        self.assertIn("已被人驳回", item["attribution_note"])

    def test_findings_without_a_target_are_implementation(self):
        [item] = check([{"message": "m"}])
        self.assertEqual(item["target"], "implementation")


class RouteTests(unittest.TestCase):
    def test_contract_beats_oracle_beats_implementation(self):
        self.assertEqual(route([{"target": "oracle"}, {"target": "contract"}]), "contract")
        self.assertEqual(route([{"target": "implementation"}, {"target": "oracle"}]), "oracle")
        self.assertEqual(route([{"target": "implementation"}]), "implementation")
        self.assertEqual(route([]), "implementation")


class LayerAttributionTests(unittest.TestCase):
    def test_layer_environment_status_does_not_require_results(self):
        for status in ("ERROR", "TIMEOUT"):
            for extra in ({}, {"results": []}):
                with self.subTest(status=status, extra=extra):
                    self.assertEqual(layer_attribution({"unit": {"status": status, **extra}}),
                                     "environment")

    def test_environment_layer_cannot_hide_other_failure_evidence(self):
        for other in ({"status": "FAILED"},
                      {"status": "FAILED", "results": [{"status": "FAIL"}]},
                      {"status": "ERROR", "oracle_problems": ["执行数为 0"]}):
            with self.subTest(other=other):
                self.assertEqual(layer_attribution({"unit": {"status": "TIMEOUT"},
                                                    "acceptance": other}), "implement")

    def test_environment_results_cannot_hide_an_unevidenced_failed_layer(self):
        layers = {"unit": {"status": "FAILED", "results": [{"status": "ERROR"}]},
                  "acceptance": {"status": "FAILED"}}
        self.assertEqual(layer_attribution(layers), "implement")

    def test_timeouts_are_environment_failures(self):
        layers = {"unit": {"status": "FAILED", "results": [{"id": "R-1", "status": "TIMEOUT"}]}}
        self.assertEqual(layer_attribution(layers), "environment")

    def test_only_environment_errors_mean_environment(self):
        layers = {"unit": {"status": "FAILED", "results": [{"id": "R-1", "status": "ERROR"}]},
                  "acceptance": {"status": "PASSED", "results": [{"id": "S-1", "status": "PASS"}]}}
        self.assertEqual(layer_attribution(layers), "environment")

    def test_a_failed_assertion_means_implement(self):
        layers = {"acceptance": {"status": "FAILED", "results": [{"id": "S-1", "status": "FAIL"},
                                                                 {"id": "S-2", "status": "ERROR"}]}}
        self.assertEqual(layer_attribution(layers), "implement")

    def test_a_count_problem_means_implement(self):
        layers = {"unit": {"status": "FAILED", "oracle_problems": ["REGRESSION-001: 执行数为 0"],
                           "results": [{"id": "REGRESSION-001", "status": "ERROR"}]}}
        self.assertEqual(layer_attribution(layers), "implement")


class SignatureTests(unittest.TestCase):
    def test_boundary_category_and_path_are_part_of_the_signature(self):
        first = {"boundary": {"violations": [{"path": "a.py", "category": "OUT_OF_SCOPE"}]}}
        for violation in ({"path": "b.py", "category": "OUT_OF_SCOPE"},
                          {"path": "a.py", "category": "UNSAFE_LINK"}):
            self.assertNotEqual(failure_signature(first),
                                failure_signature({"boundary": {"violations": [violation]}}))

    def test_contract_error_codes_are_part_of_the_signature(self):
        self.assertNotEqual(failure_signature({"contract": {"code": "A"}}),
                            failure_signature({"contract": {"code": "B"}}))

    def test_evidenced_findings_do_not_change_with_perspective(self):
        finding = {"criterion_id": "AC-1", "location": "a.py:2", "perspective": "security"}
        self.assertEqual(failure_signature({"review": {"findings": [finding]}}),
                         failure_signature({"review": {"findings": [dict(finding, perspective="tests")]}}))

    def test_order_and_duplicate_findings_do_not_change_signature(self):
        one = {"criterion_id": "AC-1", "location": "a.py:1"}
        two = {"criterion_id": "AC-2", "location": "b.py:1"}
        self.assertEqual(failure_signature({"review": {"findings": [one, two]}}),
                         failure_signature({"review": {"findings": [two, one, one]}}))

    def test_messages_do_not_change_the_signature(self):
        one = {"review": {"findings": [{"message": "a", "criterion_id": "AC-1", "location": "x.py:1"}]}}
        two = {"review": {"findings": [{"message": "b", "criterion_id": "AC-1", "location": "x.py:9"}]}}
        self.assertEqual(failure_signature(one), failure_signature(two))

    def test_a_case_turning_green_changes_the_signature(self):
        red = {"acceptance": {"status": "FAILED", "results": [{"id": "S-1", "status": "FAIL"},
                                                              {"id": "S-2", "status": "FAIL"}]}}
        better = {"acceptance": {"status": "FAILED", "results": [{"id": "S-1", "status": "PASS"},
                                                                 {"id": "S-2", "status": "FAIL"}]}}
        self.assertNotEqual(failure_signature(red), failure_signature(better))

    def test_findings_without_locations_are_told_apart_by_perspective(self):
        one = {"review": {"findings": [{"message": "a", "perspective": "security"}]}}
        two = {"review": {"findings": [{"message": "b", "perspective": "tests"}]}}
        self.assertNotEqual(failure_signature(one), failure_signature(two))


class ProgressTests(unittest.TestCase):
    def test_attempt_gaps_duplicates_and_reverse_order_break_the_streak(self):
        for attempts in ((1, 3), (1, 1), (2, 1)):
            with self.subTest(attempts=attempts):
                failures = [{"attempt": n, "signature": "a"} for n in attempts]
                self.assertIsNone(next_mode(failures, 0))

    def test_nonconsecutive_root_cause_attempt_is_not_no_progress(self):
        for attempts in ((1, 3, 4), (1, 2, 4), (1, 2, 2)):
            with self.subTest(attempts=attempts):
                failures = [{"attempt": n, "signature": "a"} for n in attempts]
                failures[-1]["mode"] = "root_cause"
                self.assertFalse(no_progress(failures, 0))

    def test_consecutive_attempts_without_epoch_remain_compatible(self):
        failures = [{"attempt": n, "signature": "a"} for n in (1, 2, 3)]
        self.assertEqual(next_mode(failures[:2], 0), "root_cause")
        failures[-1]["mode"] = "root_cause"
        self.assertTrue(no_progress(failures, 0))

    def failure(self, signature, epoch=0, mode=None, attempt=1):
        return {"attempt": attempt, "signature": signature, "epoch": epoch, "mode": mode}

    def test_root_cause_requires_two_prior_identical_failures(self):
        self.assertFalse(no_progress([self.failure("a"), self.failure("a", mode="root_cause", attempt=2)], 0))
        self.assertFalse(no_progress([self.failure("b"), self.failure("a", attempt=2),
                                      self.failure("a", mode="root_cause", attempt=3)], 0))

    def test_a_legacy_attempt_breaks_a_consecutive_streak(self):
        self.assertIsNone(next_mode([self.failure("a"), {"attempt": 2}, self.failure("a", attempt=3)], 0))

    def test_two_identical_failures_ask_for_root_cause(self):
        self.assertIsNone(next_mode([self.failure("a")], 0))
        self.assertIsNone(next_mode([self.failure("a"), self.failure("b", attempt=2)], 0))
        self.assertEqual(next_mode([self.failure("a"), self.failure("a", attempt=2)], 0), "root_cause")

    def test_an_unchanged_root_cause_attempt_is_no_progress(self):
        failures = [self.failure("a"), self.failure("a", attempt=2),
                    self.failure("a", mode="root_cause", attempt=3)]
        self.assertTrue(no_progress(failures, 0))
        failures[-1] = self.failure("b", mode="root_cause", attempt=3)
        self.assertFalse(no_progress(failures, 0))

    def test_counting_restarts_after_an_acceptance_repair(self):
        before = [self.failure("a"), self.failure("a", attempt=2)]
        self.assertEqual(next_mode(before, 0), "root_cause")
        self.assertIsNone(next_mode(before + [self.failure("a", epoch=1)], 1))
        self.assertFalse(no_progress(before + [self.failure("a", epoch=1, mode="root_cause")], 1))

    def test_legacy_failures_without_signature_are_ignored(self):
        self.assertIsNone(next_mode([{"attempt": 1}, {"attempt": 2}], 0))


if __name__ == "__main__":
    unittest.main()
