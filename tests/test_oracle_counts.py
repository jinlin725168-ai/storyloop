"""Test runners' own summaries tell how many tests really ran."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

from storyloop.models import StoryLoopError
from storyloop.oracle_counts import count_tests, validate_patterns
from storyloop.smoke_runner import run_commands


class ParserTests(unittest.TestCase):
    def test_each_builtin_runner_summary(self):
        samples = {
            "unittest": ("..s\n----\nRan 3 tests in 0.01s\n\nOK (skipped=1)\n", 2, 0),
            "pytest": ("==== 4 passed, 1 failed, 2 skipped in 0.31s ====\n", 5, 1),
            "maven": ("Tests run: 2, Failures: 0, Errors: 0, Skipped: 0\n[INFO] Results:\n"
                      "[INFO] Tests run: 7, Failures: 1, Errors: 1, Skipped: 1\n", 6, 2),
            "gradle": ("12 tests completed, 2 failed, 1 skipped\n", 11, 2),
            "jest": ("Tests:       1 failed, 1 skipped, 4 passed, 6 total\n", 5, 1),
            "vitest": (" Tests  1 failed | 3 passed (4)\n", 4, 1),
            "go": ("=== RUN   TestA\n--- PASS: TestA (0.00s)\n--- FAIL: TestB (0.00s)\n", 2, 1),
        }
        for parser, (output, executed, failed) in samples.items():
            with self.subTest(parser=parser):
                counts = count_tests(["runner"], output)
                self.assertEqual((counts["parser"], counts["executed"], counts["failed"]),
                                 (parser, executed, failed))

    def test_unrecognized_output_is_uncounted_not_zero(self):
        self.assertEqual(count_tests(["make", "test"], "all good\n")["executed"], None)

    def test_crlf_and_the_largest_of_several_matches(self):
        output = "Ran 1 test in 0.1s\r\n\r\nOK\r\n==== 9 passed in 1.0s ====\r\n"
        counts = count_tests(["x"], output)
        self.assertEqual((counts["executed"], counts["parser"]), (9, "pytest"))

    def test_project_patterns_win_for_their_command(self):
        patterns = validate_patterns([{"match": "bazel", "executed": r"Executed (?P<executed>\d+) out of"}])
        counts = count_tests(["bazel", "test", "//..."], "Executed 14 out of 14 tests\nRan 2 tests in 1s\n", patterns)
        self.assertEqual((counts["executed"], counts["parser"]), (14, "custom"))
        with self.assertRaises(StoryLoopError):
            validate_patterns([{"match": "x", "executed": r"(\d+) ran"}])  # no named group


class RunCommandsTests(unittest.TestCase):
    def test_counts_come_from_stderr_and_from_beyond_the_kept_output(self):
        script = ("import sys; print('x' * 200000); "
                  "sys.stderr.write('Ran 5 tests in 0.1s\\n\\nOK\\n')")
        with tempfile.TemporaryDirectory() as folder:
            result = run_commands(Path(folder), [[sys.executable, "-c", script]], 30)
        self.assertEqual(result["results"][0]["counts"]["executed"], 5)


if __name__ == "__main__":
    unittest.main()
