"""CI result recording, loading, lineage checks, and M6 computation."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.ci_results import load_ci_results, record_ci_result
from storyloop.cli import dispatch, parser
from storyloop.metrics_compute import local_green_ci_red
from storyloop.models import StoryLoopError
from storyloop.storage import load_json
from tests.lesson_support import _git, cleanup_repo, commit_file, git_repo, head
from tests.test_metrics_compute import rec


def _write_ci(folder: Path, filename: str, *, change: str, status: str,
              commit: str, at: str, layers: dict | None = None) -> None:
    (folder / filename).write_text(json.dumps({
        "version": 1,
        "change_id": change,
        "status": status,
        "commit": commit,
        "recorded_at": at,
        "layers": layers or {"acceptance": status, "smoke": status},
    }), encoding="utf-8")


class CiResultTests(unittest.TestCase):
    def setUp(self):
        self.project = git_repo()
        self.addCleanup(cleanup_repo, self.project)

    def test_record_writes_head_and_layer_status(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "ci-result.json"

            record_ci_result(output, self.project, {
                "change_id": "FEAT-1", "status": "FAILED",
                "acceptance": {"status": "PASSED"}, "smoke": {"status": "FAILED"},
            })

            value = load_json(output)
        self.assertEqual((value["commit"], value["layers"]["smoke"]),
                         (head(self.project), "FAILED"))
        self.assertEqual(value["version"], 1)
        self.assertTrue(value["recorded_at"].endswith("+00:00"))

    def test_record_requires_an_existing_parent(self):
        output = self.project / "missing" / "ci.json"

        with self.assertRaises(StoryLoopError):
            record_ci_result(output, self.project, {
                "change_id": "FEAT-1", "status": "PASSED",
                "acceptance": {"status": "PASSED"}, "smoke": {"status": "PASSED"},
            })

        self.assertFalse(output.exists())

    def test_ci_results_compare_real_instants_and_ignore_unrelated_commits(self):
        merge = head(self.project)
        descendant = commit_file(self.project, "later.txt", "later\n")
        branch = _git(self.project, "branch", "--show-current")
        _git(self.project, "checkout", "--orphan", "unrelated")
        _git(self.project, "commit", "--allow-empty", "-q", "-m", "unrelated")
        unrelated = head(self.project)
        _git(self.project, "checkout", "-q", branch)
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            _write_ci(folder, "ci-1.json", change="FEAT-1", status="FAILED", commit=merge,
                      at="2026-10-20T02:00:00+02:00")
            _write_ci(folder, "ci-2.json", change="FEAT-1", status="PASSED", commit=descendant,
                      at="2026-10-20T01:00:00+00:00")
            _write_ci(folder, "ci-3.json", change="FEAT-2", status="FAILED", commit=unrelated,
                      at="2026-10-20T01:00:00+00:00")
            records = [
                rec("FEAT-1", commits={"merge_commit": merge}),
                rec("FEAT-2", commits={"merge_commit": merge}),
            ]

            results, missing = load_ci_results(self.project, folder, records)

        self.assertEqual(results["FEAT-1"]["status"], "PASSED")
        self.assertNotIn("FEAT-2", results)
        self.assertIn({"change_id": "FEAT-2", "reason": "CI_COMMIT_UNRELATED"}, missing)

    def test_ci_results_reject_symlinks_oversize_and_invalid_timestamps(self):
        merge = head(self.project)
        records = [rec("FEAT-1", commits={"merge_commit": merge})]
        with tempfile.TemporaryDirectory() as folder_name, tempfile.TemporaryDirectory() as outside_name:
            folder, outside = Path(folder_name), Path(outside_name)
            _write_ci(outside, "outside.json", change="FEAT-1", status="FAILED", commit=merge,
                      at="2026-10-20T01:00:00+00:00")
            with self.subTest(case="symlink"):
                try:
                    (folder / "ci-link.json").symlink_to(outside / "outside.json")
                except (OSError, NotImplementedError) as error:
                    self.skipTest(f"Symlinks unavailable: {error}")
            (folder / "ci-large.json").write_bytes(b" " * 1_000_001)
            _write_ci(folder, "ci-invalid.json", change="FEAT-1", status="FAILED", commit=merge,
                      at="2026-10-20T01:00:00")

            results, missing = load_ci_results(self.project, folder, records)

        self.assertEqual(results, {})
        self.assertIn({"change_id": "FEAT-1", "reason": "UNREADABLE"}, missing)

    def test_git_ancestry_errors_are_unreadable_not_unrelated(self):
        records = [rec("FEAT-1", commits={"merge_commit": head(self.project)})]
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            _write_ci(folder, "ci-1.json", change="FEAT-1", status="FAILED",
                      commit="0" * 40, at="2026-10-20T01:00:00+00:00")

            results, missing = load_ci_results(self.project, folder, records)

        self.assertEqual(results, {})
        self.assertEqual(missing, [{"change_id": "FEAT-1", "reason": "UNREADABLE"}])

    def test_invalid_ci_schema_does_not_become_a_valid_sample(self):
        cases = (
            {"status": "CANCELLED"}, {"layers": {}},
            {"layers": {"acceptance": "PASSED"}},
            {"layers": {"acceptance": "SKIPPED", "smoke": "PASSED"}},
            {"layers": {"acceptance": "PASSED", "smoke": "CANCELLED"}},
            {"layers": {"acceptance": ["PASSED"], "smoke": "PASSED"}},
            {"version": True}, {"version": 2},
        )
        merge = head(self.project)
        records = [rec("A", commits={"merge_commit": merge})]
        for invalid in cases:
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                value = {"version": 1, "change_id": "A", "status": "PASSED", "commit": merge,
                         "recorded_at": "2026-10-20T02:00:00+00:00",
                         "layers": {"acceptance": "PASSED", "smoke": "PASSED"}, **invalid}
                (folder / "ci-new.json").write_text(json.dumps(value), encoding="utf-8")

                results, missing = load_ci_results(self.project, folder, records)

                self.assertEqual(results, {})
                self.assertEqual(missing, [{"change_id": "A", "reason": "UNREADABLE"}])
                self.assertIsNone(local_green_ci_red(results, records)["value"])

                _write_ci(folder, "ci-old.json", change="A", status="FAILED", commit=merge,
                          at="2026-10-20T01:00:00+00:00")
                results, missing = load_ci_results(self.project, folder, records)

                self.assertEqual(results["A"]["status"], "FAILED")
                self.assertEqual(local_green_ci_red(results, records)["value"], 1.0)
                self.assertEqual(missing, [{"change_id": "A", "reason": "UNREADABLE"}])

    def test_ci_accepts_real_error_and_skipped_layer_statuses(self):
        merge = head(self.project)
        records = [rec("A", commits={"merge_commit": merge})]
        for acceptance, smoke, status in (
            ("ERROR", "ERROR", "FAILED"), ("PASSED", "SKIPPED", "PASSED"),
        ):
            with self.subTest(acceptance=acceptance), tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                _write_ci(folder, "ci-result.json", change="A", status=status, commit=merge,
                          at="2026-10-20T01:00:00+00:00",
                          layers={"acceptance": acceptance, "smoke": smoke})

                results, missing = load_ci_results(self.project, folder, records)

                self.assertEqual(results["A"]["status"], status)
                self.assertEqual(missing, [])

    def test_local_green_ci_red_counts_only_passed_changes_with_results(self):
        result = local_green_ci_red({
            "A": {"status": "FAILED", "layers": {"acceptance": "PASSED", "smoke": "FAILED"}},
            "B": {"status": "PASSED", "layers": {"acceptance": "PASSED", "smoke": "PASSED"}},
            "C": {"status": "FAILED", "layers": {"acceptance": "FAILED", "smoke": "PASSED"}},
        }, [rec("A"), rec("B"), rec("C", status="NO_PROGRESS"), rec("D")])

        self.assertEqual((result["value"], result["numerator"], result["denominator"]),
                         (0.5, 1, 2))
        self.assertEqual(result["by"], {"smoke": 1})
        self.assertEqual(result["changes"], ["A"])

    def test_local_green_ci_red_without_results_is_not_configured(self):
        result = local_green_ci_red(None, [rec("A")])

        self.assertIsNone(result["value"])
        self.assertEqual(result["reason"], "CI_RESULTS_NOT_PROVIDED")

    def test_cli_record_flag_records_returned_ci_result(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "ci.json"
            args = parser().parse_args([
                "ci", str(self.project), "--change", "FEAT-1", "--record", str(output), "--json",
            ])
            ci_result = {
                "change_id": "FEAT-1", "status": "PASSED",
                "acceptance": {"status": "PASSED"}, "smoke": {"status": "SKIPPED"},
            }

            with patch("storyloop.ci_command.run_ci", return_value=ci_result):
                returned = dispatch(args)

            recorded = load_json(output)
        self.assertEqual(returned, ci_result)
        self.assertEqual((recorded["change_id"], recorded["layers"]["smoke"]),
                         ("FEAT-1", "SKIPPED"))


if __name__ == "__main__":
    unittest.main()
