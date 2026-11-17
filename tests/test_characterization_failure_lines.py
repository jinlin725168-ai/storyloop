"""A red characterization gate names the failing tests (2026-11-17 L3 #516).

The stop showed "特征化测试在旧代码上不绿" with an empty evidence list; the failing test
names were only in the 1500-line pytest output saved in the run state.
"""

import unittest

from storyloop.characterization_baseline import failure_lines


class FailureLineTests(unittest.TestCase):
    def test_failing_results_yield_their_summary_lines(self):
        unit = {"status": "FAILED", "results": [
            {"id": "REGRESSION-001", "status": "FAIL", "stdout": (
                "....F.\n=========================== short test summary info ====\n"
                "FAILED tests/test_cli_insert_characterize.py::test_insert_ignore - AssertionError: assert '\\n' == ''\n"
                "FAILED tests/test_cli_insert_characterize.py::test_insert_csv\n"
                "2 failed, 1507 passed in 9.1s\n"), "stderr": ""},
            {"id": "REGRESSION-002", "status": "PASS", "stdout": "FAILED nothing\n"}]}
        lines = failure_lines(unit)
        self.assertEqual(lines[0], "REGRESSION-001: FAILED tests/test_cli_insert_characterize.py::"
                                   "test_insert_ignore - AssertionError: assert '\\n' == ''")
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[-1].endswith("2 failed, 1507 passed in 9.1s"))

    def test_unrecognised_output_falls_back_to_the_tail(self):
        unit = {"status": "FAILED", "results": [
            {"id": "U", "status": "FAIL", "returncode": 2, "stdout": "a\nb\nImportError: x\n", "stderr": ""}]}
        self.assertEqual(failure_lines(unit), ["U: a", "U: b", "U: ImportError: x"])


if __name__ == "__main__":
    unittest.main()
