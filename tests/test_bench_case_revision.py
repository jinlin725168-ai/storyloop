"""A v2.1 Case keeps the v2 gate while requiring AC-level entry coverage."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from storyloop.bench_case import load_case
from storyloop.bench_run import compare_runs, run_bench
from storyloop.bench_worker import _execute_change
from tests.test_bench_case import case_fixture, v2_case_fixture


class BenchCaseRevisionTests(unittest.TestCase):
    def test_five_approved_cases_cover_each_automatic_criterion(self):
        cases_root = Path(__file__).resolve().parents[1] / "bench/cases"
        for source in sorted(cases_root.glob("*/v2")):
            with self.subTest(case=source.parent.name):
                case = load_case(source)
                self.assertEqual(case["manifest"]["case_revision"], "2.1")
                self.assertTrue(case["contract_approved"])
                for story in case["contract"]["product"]["stories"]:
                    entry_ids = {entry["id"] for entry in story["verification_entries"]}
                    for criterion in story["acceptance"]:
                        if criterion["kind"] == "automated":
                            self.assertTrue(criterion["entry_ids"])
                            self.assertTrue(set(criterion["entry_ids"]) <= entry_ids)

    def source(self, root):
        source = v2_case_fixture(case_fixture(root), approved=True)
        manifest_path = source / "case.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["case_revision"] = "2.1"
        manifest_path.write_text(json.dumps(manifest))
        return source

    def test_revision_is_loaded_and_recorded_without_changing_v2_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = self.source(root)
            self.assertEqual(load_case(source)["manifest"]["case_revision"], "2.1")
            outcome = {"status": "PASSED", "events": [], "calls": 1,
                       "agent_ms": 1, "wall_ms": 2}
            result = run_bench([source], "a" * 40, "", "test", "fixture",
                               {"max_calls": 10}, lambda *_: outcome, lambda *_: True,
                               root, scope="base-only")
            self.assertEqual(result["case_version"], 2)
            self.assertEqual(result["case_revision"], "2.1")
            self.assertEqual(result["cases"][0]["case_revision"], "2.1")
            old = {**result, "case_revision": None}
            self.assertIn("case_revision", compare_runs(result, old)["reasons"])

    def test_worker_requires_entry_coverage_only_for_revision_21(self):
        create = Mock(return_value={"change_id": "FIX-20261107-demo"})
        services = {"create_change": create, "approve_change": Mock(),
                    "load_change": Mock(), "install_smoke_suite": Mock(),
                    "run_change": Mock(), "approve_design": Mock(), "load_run_state": Mock()}
        contract = {"change_id": "FIX-20261107-demo", "product": {}}
        for revision, expected in ((None, False), ("2.1", True)):
            with self.subTest(revision=revision):
                create.reset_mock()
                with patch("storyloop.bench_worker._run_until_stop", return_value={}):
                    _execute_change(Path("/tmp"), contract, "unused", 2, "generated",
                                    case_revision=revision, **services)
                self.assertEqual(create.call_args.kwargs["require_entry_coverage"], expected)


if __name__ == "__main__":
    unittest.main()
