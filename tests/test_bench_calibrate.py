"""Calibration executes frozen checks in disposable Base and Solution trees."""

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import sys

from storyloop.bench_calibrate import calibrate_case
from tests.test_bench_case import case_fixture, stale_approval_fixture, v2_case_fixture


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def calibration_fixture(root: Path) -> tuple[Path, Path]:
    repository = root / "repository"
    repository.mkdir()
    git(repository, "init", "-q")
    git(repository, "config", "user.name", "Bench Test")
    git(repository, "config", "user.email", "bench@example.invalid")
    (repository / "feature.py").write_text("VALUE = 1\n")
    git(repository, "add", ".")
    git(repository, "commit", "-qm", "base")
    base = git(repository, "rev-parse", "HEAD")
    (repository / "feature.py").write_text("VALUE = 2\n")
    git(repository, "commit", "-qam", "solution")
    solution = git(repository, "rev-parse", "HEAD")
    source = case_fixture(root)
    script = ("from feature import VALUE\nimport sys\n"
              "assert VALUE == 2 if sys.argv[1] == 'f2p' else VALUE >= 1\n")
    content = script.encode()
    (source / "checks/test_example.py").write_bytes(content)
    gold = json.loads((source / "gold.json").read_text())
    for check in gold["checks"]:
        check["argv"] = ["{python}", "checks/test_example.py", check["kind"].lower()]
    gold_bytes = json.dumps(gold, sort_keys=True).encode()
    (source / "gold.json").write_bytes(gold_bytes)
    manifest = json.loads((source / "case.json").read_text())
    manifest["base_commit"], manifest["solution_commit"] = base, solution
    manifest["gold_sha256"] = hashlib.sha256(gold_bytes).hexdigest()
    manifest["suite_files"][0]["sha256"] = hashlib.sha256(content).hexdigest()
    manifest["suite_sha256"] = hashlib.sha256(b"checks/test_example.py\0" + content).hexdigest()
    (source / "case.json").write_text(json.dumps(manifest, sort_keys=True))
    return source, repository


class BenchCalibrationTests(unittest.TestCase):
    def test_stale_approved_v2_is_marked_in_calibration_record(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, repository = calibration_fixture(root)
            source = stale_approval_fixture(v2_case_fixture(source, approved=True))
            result = calibrate_case(source, repository, root)
            self.assertEqual(result["status"], "FAILED")
            self.assertIs(result["approval_stale"], True)
            self.assertIs(result["contract_approved"], False)
            self.assertIn("approval_stale", result["reasons"])
            self.assertIs(json.loads(Path(result["record_path"]).read_text())["approval_stale"], True)

    def test_unapproved_v2_calibrates_and_records_contract_status(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, repository = calibration_fixture(root)
            source = v2_case_fixture(source)
            result = calibrate_case(source, repository, root, python=Path(sys.executable))
            self.assertEqual(result["status"], "CALIBRATED", result)
            self.assertIs(result["contract_approved"], False)
            self.assertIs(json.loads(Path(result["record_path"]).read_text())["contract_approved"], False)

    def test_approved_v2_calibration_records_true(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, repository = calibration_fixture(root)
            result = calibrate_case(v2_case_fixture(source, approved=True), repository, root)
            self.assertEqual(result["status"], "CALIBRATED", result)
            self.assertIs(result["contract_approved"], True)

    def test_v2_cli_calibrates_without_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, repository = calibration_fixture(root)
            v2_case_fixture(source)
            completed = subprocess.run(
                [sys.executable, "-m", "storyloop", "bench", "--json", "calibrate", "example",
                 "--case-version", "2", "--repository", str(repository), "--python", sys.executable,
                 "--cases-root", str(root / "bench/cases"), "--output-root", str(root)],
                capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn('"contract_approved": false', completed.stdout)

    def test_f2p_and_p2p_calibrate_and_record_exit_codes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, repository = calibration_fixture(root)
            before = git(repository, "status", "--porcelain")
            result = calibrate_case(source, repository, root)
            self.assertEqual(result["status"], "CALIBRATED", result)
            self.assertIs(result["contract_approved"], True)
            self.assertEqual([(row["base"]["returncode"], row["solution"]["returncode"])
                              for row in result["checks"]], [(1, 0), (0, 0)])
            self.assertEqual(git(repository, "status", "--porcelain"), before)
            self.assertTrue(Path(result["record_path"]).is_file())

    def test_failed_f2p_is_recorded_without_relabeling_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, repository = calibration_fixture(root)
            script = (source / "checks/test_example.py").read_text().replace("VALUE == 2", "VALUE >= 1")
            content = script.encode()
            (source / "checks/test_example.py").write_bytes(content)
            manifest = json.loads((source / "case.json").read_text())
            manifest["suite_files"][0]["sha256"] = hashlib.sha256(content).hexdigest()
            manifest["suite_sha256"] = hashlib.sha256(b"checks/test_example.py\0" + content).hexdigest()
            (source / "case.json").write_text(json.dumps(manifest))
            result = calibrate_case(source, repository, root)
            self.assertEqual(result["status"], "FAILED")
            self.assertIn("F2P-1", result["reasons"][0])

    def test_gold_imports_helper_from_its_own_historical_tree(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, repository = calibration_fixture(root)
            package = repository / "storyloop/testing/smoke_harness"
            package.mkdir(parents=True)
            for path in (repository / "storyloop", repository / "storyloop/testing", package):
                (path / "__init__.py").write_text("")
            (package / "v1.py").write_text("VALUE = 1\n")
            git(repository, "add", ".")
            git(repository, "commit", "-qm", "historical base")
            base = git(repository, "rev-parse", "HEAD")
            (package / "v1.py").write_text("VALUE = 2\n")
            git(repository, "commit", "-qam", "historical solution")
            solution = git(repository, "rev-parse", "HEAD")
            script = ("from storyloop.testing.smoke_harness.v1 import VALUE\nimport sys\n"
                      "assert VALUE == 2 if sys.argv[1] == 'f2p' else VALUE >= 1\n")
            content = script.encode()
            (source / "checks/test_example.py").write_bytes(content)
            manifest = json.loads((source / "case.json").read_text())
            manifest["base_commit"], manifest["solution_commit"] = base, solution
            manifest["suite_files"][0]["sha256"] = hashlib.sha256(content).hexdigest()
            manifest["suite_sha256"] = hashlib.sha256(b"checks/test_example.py\0" + content).hexdigest()
            (source / "case.json").write_text(json.dumps(manifest))
            result = calibrate_case(source, repository, root, python=Path(sys.executable))
            self.assertEqual(result["status"], "CALIBRATED", result["reasons"])
            self.assertEqual(result["checks"][0]["solution"]["returncode"], 0)
            from storyloop.bench_calibrate import judge_gold
            self.assertTrue(judge_gold(source, repository, python=Path(sys.executable))["passed"])

    def test_record_contains_interpreter_and_comparison_rejects_different_one(self):
        from storyloop.bench_calibrate import compare_calibrations

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, repository = calibration_fixture(root)
            record = calibrate_case(source, repository, root, python=Path(sys.executable))
            self.assertEqual(record["python"]["path"], sys.executable)
            self.assertEqual(record["python"]["version"],
                             f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
            changed = {**record, "python": {**record["python"], "path": "/other/python"}}
            self.assertEqual(compare_calibrations(record, changed)["status"], "NOT_COMPARABLE")
            self.assertIn("python", compare_calibrations(record, changed)["reasons"])


if __name__ == "__main__":
    unittest.main()
