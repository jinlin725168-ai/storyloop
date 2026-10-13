"""Acceptance tests are fast or heavy, and one test never mixes must-be-red with must-be-green rows."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from storyloop.changes import create_change
from storyloop.models import StoryLoopError
from storyloop.smoke import install_smoke_suite
from tests.support import product
from tests.test_baseline_behavior import repository

NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)


def two_row_product() -> dict:
    """AC-1 asserts new behaviour (red first); AC-2 asserts that existing behaviour keeps holding."""
    value = product()
    value["stories"][0]["acceptance"].append({
        "id": "AC-2", "given": "an existing room", "when": "listed", "then": "it is still listed",
        "kind": "automated", "row_type": "regression"})
    return value


def approved(root: Path, value: dict) -> str:
    repository(root)
    bootstrap_project(root, {"test_commands": []})
    change_id = create_change(root, "rooms", value, now=lambda: NOW)["change_id"]
    approve_change(root, change_id, now=lambda: NOW)
    return change_id


def test_entry(test_id: str, criteria: list[str], tier: str | None = None) -> dict:
    entry = {"id": test_id, "criteria": criteria, "command": ["{python}", "-V"], "timeout_seconds": 10}
    if tier:
        entry["tier"] = tier
    return entry


class ManifestTierTests(unittest.TestCase):
    def test_a_test_cannot_mix_expected_states(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root, two_row_product())
            files = [{"path": "t.py", "content": "pass\n"}]
            with self.assertRaisesRegex(StoryLoopError, "expected"):
                install_smoke_suite(root, change_id, files, {"version": 1, "tests": [
                    test_entry("T1", ["AC-1", "AC-2"])]})
            install_smoke_suite(root, change_id, files, {"version": 1, "tests": [
                test_entry("T1", ["AC-1"]), test_entry("T2", ["AC-2"])]})

    def test_a_heavy_criterion_needs_a_heavy_test(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            value = product()
            value["stories"][0]["acceptance"][0]["oracle_class"] = "auto-heavy"
            change_id = approved(root, value)
            files = [{"path": "t.py", "content": "pass\n"}]
            with self.assertRaisesRegex(StoryLoopError, "tier"):
                install_smoke_suite(root, change_id, files, {"version": 1, "tests": [test_entry("T1", ["AC-1"])]})
            install_smoke_suite(root, change_id, files, {"version": 1, "tests": [
                test_entry("T1", ["AC-1"], "heavy")]})


if __name__ == "__main__":
    unittest.main()
