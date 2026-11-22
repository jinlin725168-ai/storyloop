"""Issue #62: an explicit review demand for tests cannot be ignored round after round."""
import unittest

from storyloop.project_loop_failures import unaddressed_tests_first

DEMAND = {"perspective": "tests", "disposition": "MUST_FIX", "message": "Tests First 为空，新增行为没有单元测试"}


def state(*findings):
    return {"failures": [{"attempt": 1, "review": {"verdict": "fail", "findings": list(findings)}}]}


def boundary(*categories):
    return {"allowed": [{"path": f"p{index}", "category": category} for index, category in enumerate(categories)]}


class TestsFirstGuardTests(unittest.TestCase):
    def test_an_ignored_demand_returns_to_implement_with_the_finding(self):
        failure = unaddressed_tests_first(state(DEMAND), boundary("FEATURE"))
        self.assertEqual(failure["tests_first"]["status"], "FAILED")
        self.assertEqual(failure["tests_first"]["unaddressed"], [DEMAND["message"]])

    def test_a_test_change_or_no_demand_lets_the_attempt_through(self):
        self.assertIsNone(unaddressed_tests_first(state(DEMAND), boundary("FEATURE", "TEST")))
        self.assertIsNone(unaddressed_tests_first(state({**DEMAND, "disposition": "ADVISORY"}), boundary("FEATURE")))
        self.assertIsNone(unaddressed_tests_first(state({**DEMAND, "perspective": "security"}), boundary("FEATURE")))
        self.assertIsNone(unaddressed_tests_first({"failures": []}, boundary("FEATURE")))
        self.assertIsNone(unaddressed_tests_first({"failures": [{"attempt": 1, "acceptance": "FAILED"}]},
                                                  boundary("FEATURE")))


if __name__ == "__main__":
    unittest.main()
