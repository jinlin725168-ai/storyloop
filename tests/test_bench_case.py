"""Frozen bench Case contracts reject missing or unsafe evidence."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import sys

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
        "python": {"version": f"{sys.version_info.major}.{sys.version_info.minor}",
                   "dependencies": "Python standard library; source tree modules"},
        "legacy_seam": True,
    })
    return source


def v2_case_fixture(source: Path, *, approved: bool = False) -> Path:
    """Derive a v2 fixture with an entry-linked suite from an existing v1 fixture."""
    source = source.rename(source.with_name("v2"))
    contract = json.loads((source / "contract.json").read_text())
    contract["product"]["stories"][0]["verification_entries"] = [
        {"id": "VE-1", "kind": "public_function", "locator": "feature.value",
         "observable": "The value is 2", "reason": "Deterministic public seam"}]
    if approved:
        contract["approval"]["contract_digest"] = contract_digest(contract["product"])
    else:
        contract.pop("approval")
    contract_bytes = _bytes(source / "contract.json", contract)
    suite_path = "checks/acceptance/changes/FEAT-example/manifest.json"
    suite_bytes = _bytes(source / suite_path, {"version": 1, "tests": [
        {"id": "example", "criteria": ["AC-1"], "entry_ids": ["VE-1"],
         "command": ["{python}", "checks/test_example.py"], "timeout_seconds": 10}]})
    gold = json.loads((source / "gold.json").read_text())
    gold["evidence"][0]["sha256"] = hashlib.sha256(contract_bytes).hexdigest()
    gold_bytes = _bytes(source / "gold.json", gold)
    manifest = json.loads((source / "case.json").read_text())
    manifest["case_version"], manifest["legacy_seam"] = 2, False
    manifest["contract_sha256"] = hashlib.sha256(contract_bytes).hexdigest()
    manifest["gold_sha256"] = hashlib.sha256(gold_bytes).hexdigest()
    manifest["suite_files"].append({"path": suite_path,
                                    "sha256": hashlib.sha256(suite_bytes).hexdigest()})
    manifest["suite_files"].sort(key=lambda item: item["path"])
    suite_hash = hashlib.sha256()
    for item in manifest["suite_files"]:
        suite_hash.update(item["path"].encode() + b"\0" + (source / item["path"]).read_bytes())
    manifest["suite_sha256"] = suite_hash.hexdigest()
    _bytes(source / "case.json", manifest)
    return source


class BenchCaseTests(unittest.TestCase):
    def test_unapproved_v1_remains_rejected_for_calibration_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            source = case_fixture(Path(folder))
            contract = json.loads((source / "contract.json").read_text())
            contract.pop("approval")
            content = _bytes(source / "contract.json", contract)
            manifest = json.loads((source / "case.json").read_text())
            manifest["contract_sha256"] = hashlib.sha256(content).hexdigest()
            _bytes(source / "case.json", manifest)
            with self.assertRaises(StoryLoopError):
                load_case(source, allow_unapproved_v2=True)

    def test_unapproved_v2_requires_explicit_calibration_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            source = v2_case_fixture(case_fixture(Path(folder)))
            with self.assertRaises(StoryLoopError):
                load_case(source)
            self.assertFalse(load_case(source, allow_unapproved_v2=True)["contract_approved"])

    def test_approved_v2_loads_for_run(self):
        with tempfile.TemporaryDirectory() as folder:
            source = v2_case_fixture(case_fixture(Path(folder)), approved=True)
            self.assertTrue(load_case(source)["contract_approved"])

    def test_load_valid_v1_case(self):
        with tempfile.TemporaryDirectory() as folder:
            case = load_case(case_fixture(Path(folder)))
            self.assertEqual(case["manifest"]["case_version"], 1)
            self.assertEqual(len(case["gold"]["checks"]), 2)

    def test_missing_field_bad_hash_and_escape_are_rejected(self):
        for mutation in ("missing", "hash", "escape", "python"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as folder:
                source = case_fixture(Path(folder))
                manifest = json.loads((source / "case.json").read_text())
                if mutation == "missing":
                    manifest.pop("base_commit")
                elif mutation == "hash":
                    manifest["contract_sha256"] = "0" * 64
                else:
                    if mutation == "escape":
                        manifest["suite_files"][0]["path"] = "../outside.py"
                    else:
                        manifest["python"]["version"] = "3.x"
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
