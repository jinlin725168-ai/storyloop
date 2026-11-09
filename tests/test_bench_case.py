"""Frozen bench Case contracts reject missing or unsafe evidence."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from storyloop.bench_case import load_case
from storyloop.models import StoryLoopError
from storyloop.changes import contract_digest
from tests.support import product


def _bytes(path: Path, value: dict | str) -> bytes:
    content = (json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
               if isinstance(value, dict) else value.encode())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


def case_fixture(root: Path) -> Path:
    source = root / "bench/cases/example/v1"
    contract = {"change_id": "FEAT-example", "product": product(),
                "approval": {"contract_digest": contract_digest(product()),
                             "approved_by": "user", "approved_at": "2026-01-01T00:00:00Z"}}
    contract_bytes = _bytes(source / "contract.json", contract)
    check = _bytes(source / "checks/test_example.py", "raise SystemExit(1)\n")
    gold = {"schema_version": 1,
            "evidence": [{"evidence_id": "E-1", "path": "contract.json",
                          "sha256": hashlib.sha256(contract_bytes).hexdigest()}],
            "requirements": [{"requirement_id": "AC-1", "evidence_ids": ["E-1"],
                              "check_ids": ["F2P-1", "P2P-1"]}],
            "checks": [{"check_id": "F2P-1", "kind": "F2P",
                        "argv": ["{python}", "checks/test_example.py"], "timeout_seconds": 10},
                       {"check_id": "P2P-1", "kind": "P2P",
                        "argv": ["{python}", "checks/test_example.py"], "timeout_seconds": 10}]}
    gold_bytes = _bytes(source / "gold.json", gold)
    _bytes(source / "case.json", {
        "schema_version": 1, "case_id": "example", "case_version": 1,
        "repository_id": "fixture", "base_commit": "a" * 40, "solution_commit": "b" * 40,
        "contract_sha256": hashlib.sha256(contract_bytes).hexdigest(),
        "gold_sha256": hashlib.sha256(gold_bytes).hexdigest(),
        "suite_files": [{"path": "checks/test_example.py", "sha256": hashlib.sha256(check).hexdigest()}],
        "suite_sha256": hashlib.sha256(b"checks/test_example.py\0" + check).hexdigest(),
        "allowed": {"adapters": ["test"], "models": ["fixture"],
                    "max_calls": 10, "max_cost_usd": None},
        "legacy_seam": True,
    })
    return source


class BenchCaseTests(unittest.TestCase):
    def test_load_valid_v1_case(self):
        with tempfile.TemporaryDirectory() as folder:
            case = load_case(case_fixture(Path(folder)))
            self.assertEqual(case["manifest"]["case_version"], 1)
            self.assertEqual(len(case["gold"]["checks"]), 2)

    def test_missing_field_bad_hash_and_escape_are_rejected(self):
        for mutation in ("missing", "hash", "escape"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as folder:
                source = case_fixture(Path(folder))
                manifest = json.loads((source / "case.json").read_text())
                if mutation == "missing":
                    manifest.pop("base_commit")
                elif mutation == "hash":
                    manifest["contract_sha256"] = "0" * 64
                else:
                    manifest["suite_files"][0]["path"] = "../outside.py"
                _bytes(source / "case.json", manifest)
                with self.assertRaises(StoryLoopError):
                    load_case(source)

    def test_symlink_and_unmapped_check_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            source = case_fixture(Path(folder))
            (source / "checks/test_example.py").unlink()
            (source / "checks/test_example.py").symlink_to(source / "contract.json")
            with self.assertRaises(StoryLoopError):
                load_case(source)
        with tempfile.TemporaryDirectory() as folder:
            source = case_fixture(Path(folder))
            gold = json.loads((source / "gold.json").read_text())
            gold["requirements"][0]["check_ids"] = ["missing"]
            gold_bytes = _bytes(source / "gold.json", gold)
            manifest = json.loads((source / "case.json").read_text())
            manifest["gold_sha256"] = hashlib.sha256(gold_bytes).hexdigest()
            _bytes(source / "case.json", manifest)
            with self.assertRaises(StoryLoopError):
                load_case(source)


if __name__ == "__main__":
    unittest.main()
