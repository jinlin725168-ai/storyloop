"""Existing bench report guarantees that must survive summary rendering."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class BenchReportRecordCharacterization(unittest.TestCase):
    def _report(self, root: Path, run_id: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "storyloop", "bench", "--json", "report",
             run_id, "--output-root", str(root)],
            capture_output=True, text=True, check=False,
        )

    def test_json_retains_original_run_fields_without_writing_record(self):
        record = {
            "run_id": "r123", "case_revision": "2.1", "scope": "base-only",
            "suite_source": "generated", "stage_gate": {"passed": True},
            "cases": [{"case_id": "alpha", "attempts": [
                {"verdict": "PASSED", "gold_passed": True,
                 "termination_reason": None, "nonroutine_count": 0}
            ]}],
        }
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / ".storyloop/bench/runs/r123.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
            before = path.read_bytes()

            result = self._report(root, "r123")

            self.assertEqual(result.returncode, 0, result.stderr)
            rendered = json.loads(result.stdout)
            for key, value in record.items():
                with self.subTest(field=key):
                    self.assertIn(key, rendered)
                    self.assertEqual(rendered[key], value)
            self.assertEqual(path.read_bytes(), before)

    def test_missing_run_keeps_existing_error_exit(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self._report(Path(folder), "missing")

        self.assertEqual(result.returncode, 2)
        self.assertIn("Run record is unavailable", result.stderr)


if __name__ == "__main__":
    unittest.main()
