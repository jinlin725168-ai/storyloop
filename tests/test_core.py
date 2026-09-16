"""Requirements for strict contracts and honest external check outcomes."""
import json
import tempfile
import unittest
from pathlib import Path
from tests.support import product, check
from storyloop.models import StoryLoopError, validate_product, validate_checks
from storyloop.storage import initialize, approval_digest, locked
from storyloop.verification import run_check


class ContractTests(unittest.TestCase):
    def test_rejects_duplicate_acceptance_ids(self):
        value = product()
        value["stories"][0]["acceptance"] *= 2
        with self.assertRaises(StoryLoopError):
            validate_product(value)

    def test_rejects_forward_dependencies_and_empty_business_outcome(self):
        value = product()
        value["stories"][0]["depends_on"] = ["ST-2"]
        with self.assertRaises(StoryLoopError):
            validate_product(value)
        value = product()
        value["stories"][0]["acceptance"][0]["then"] = " "
        with self.assertRaises(StoryLoopError):
            validate_product(value)

    def test_requires_executable_coverage_for_every_automatic_criterion(self):
        with self.assertRaises(StoryLoopError):
            validate_checks({"version": 1, "checks": []}, product())

    def test_rejects_claiming_coverage_for_unknown_criterion(self):
        value = check()
        value["criteria"] = ["AC-missing"]
        with self.assertRaises(StoryLoopError):
            validate_checks({"version": 1, "checks": [value]}, product())


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        initialize(self.project)

    def tearDown(self):
        self.temp.cleanup()

    def test_real_behavioral_failure_is_red(self):
        self.assertEqual(run_check(self.project, check(status="fail"))["outcome"], "RED")

    def test_skipped_test_is_neither_red_nor_green(self):
        self.assertEqual(run_check(self.project, check(status="skip"))["outcome"], "BLOCKED")

    def test_inconsistent_exit_code_cannot_fake_green(self):
        value = check()
        value["command"][-1] = value["command"][-1].replace("sys.exit(0)", "sys.exit(1)")
        self.assertEqual(run_check(self.project, value)["outcome"], "BLOCKED")

    def test_missing_expected_test_and_malformed_json_block(self):
        value = check()
        value["expected_tests"] = ["not-executed"]
        self.assertEqual(run_check(self.project, value)["outcome"], "BLOCKED")
        value["command"][-1] = "print('not json')"
        self.assertEqual(run_check(self.project, value)["outcome"], "BLOCKED")

    def test_environment_error_and_timeout_block(self):
        value = check()
        value["command"] = ["definitely-not-an-installed-program-12345"]
        self.assertEqual(run_check(self.project, value)["outcome"], "BLOCKED")
        value = check()
        value["timeout_seconds"] = 1
        value["command"][-1] = "import time; time.sleep(5)"
        self.assertEqual(run_check(self.project, value)["outcome"], "BLOCKED")

    def test_approval_digest_covers_qa_and_config(self):
        before = approval_digest(self.project)
        (self.project / "qa" / "check.py").write_text("print('changed')")
        self.assertNotEqual(before, approval_digest(self.project))

    def test_second_runner_is_rejected(self):
        with locked(self.project):
            with self.assertRaises(StoryLoopError):
                with locked(self.project):
                    pass


if __name__ == "__main__":
    unittest.main()
