"""Calibration executes frozen checks in disposable Base and Solution trees."""

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from storyloop.bench_calibrate import calibrate_case
from tests.test_bench_case import case_fixture


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
    def test_f2p_and_p2p_calibrate_and_record_exit_codes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, repository = calibration_fixture(root)
            before = git(repository, "status", "--porcelain")
            result = calibrate_case(source, repository, root)
            self.assertEqual(result["status"], "CALIBRATED", result)
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

    def test_candidate_helper_import_is_rejected_before_execution(self):
        for discover in (False, True):
            with self.subTest(discover=discover), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source, repository = calibration_fixture(root)
                content = b"from storyloop.testing.smoke_harness.v1 import precondition\n"
                (source / "checks/test_example.py").write_bytes(content)
                manifest = json.loads((source / "case.json").read_text())
                manifest["suite_files"][0]["sha256"] = hashlib.sha256(content).hexdigest()
                manifest["suite_sha256"] = hashlib.sha256(b"checks/test_example.py\0" + content).hexdigest()
                if discover:
                    gold = json.loads((source / "gold.json").read_text())
                    for check in gold["checks"]:
                        check["argv"] = ["{python}", "-m", "unittest", "discover", "-s", "checks",
                                         "-p", "test_example.py"]
                    gold_bytes = json.dumps(gold).encode()
                    (source / "gold.json").write_bytes(gold_bytes)
                    manifest["gold_sha256"] = hashlib.sha256(gold_bytes).hexdigest()
                (source / "case.json").write_text(json.dumps(manifest))
                result = calibrate_case(source, repository, root)
                self.assertEqual(result["status"], "FAILED")
                self.assertEqual(result["checks"][0]["solution"]["error"], "HARNESS_SHADOWED")
                self.assertIsNone(result["checks"][0]["solution"]["returncode"])


if __name__ == "__main__":
    unittest.main()
