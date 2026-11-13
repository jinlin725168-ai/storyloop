"""Behavior claims cover every declared characterization test without a one-claim limit."""

import unittest

from storyloop.characterization_locks import validate_behavior_claims
from storyloop.models import StoryLoopError


class BehaviorClaimValidationTests(unittest.TestCase):
    def test_multiple_behavior_claims_may_share_one_test_file(self):
        test = "tests/test_characterize_service.py"
        claims = [{"test": test, "behavior": behavior, "criterion_ids": [], "symbols": []}
                  for behavior in ("old success result", "old error result")]
        validate_behavior_claims({"tests": [test], "claims": claims}, set())

    def test_each_test_file_requires_a_behavior_claim(self):
        tests = ["tests/test_characterize_service.py", "tests/test_characterize_other.py"]
        claim = {"test": tests[0], "behavior": "old success result", "criterion_ids": [], "symbols": []}
        with self.assertRaises(StoryLoopError):
            validate_behavior_claims({"tests": tests, "claims": [claim]}, set())

    def test_behavior_claim_cannot_name_an_undeclared_test_file(self):
        test = "tests/test_characterize_service.py"
        claims = [{"test": path, "behavior": "old behavior", "criterion_ids": [], "symbols": []}
                  for path in (test, "tests/test_other.py")]
        with self.assertRaises(StoryLoopError):
            validate_behavior_claims({"tests": [test], "claims": claims}, set())
