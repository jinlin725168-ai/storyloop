from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from storyloop.report_evidence import collect_evidence
from storyloop.report_reconciliation import reconcile


LAYERS = {name: {"status": "PASSED", "results": []} for name in
          ("unit", "integration", "acceptance", "smoke", "checks")}


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class ReportEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "test@example.com")
        git(self.root, "config", "user.name", "Test")
        (self.root / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
        git(self.root, "add", "tracked.py")
        git(self.root, "commit", "-q", "-m", "base")
        self.base = git(self.root, "rev-parse", "HEAD")
        self.change = {"change_id": "FEAT-1", "approval": {"contract_digest": "approved"}}

    def tearDown(self):
        self.temporary.cleanup()

    def _run(self, **updates: object) -> None:
        state = {"version": 1, "change_id": "FEAT-1", "status": "PASSED",
                 "source_head": self.base, "facts": [], "failures": [],
                 "planning": {"phase": "done"}, "answers": [{"secret": "never-copy"}]}
        state.update(updates)
        write_json(self.root / ".storyloop/state/runs/FEAT-1.json", state)

    def _report(self, **updates: object) -> dict:
        report = {"status": "PASSED", "implementation_commit": git(self.root, "rev-parse", "HEAD"),
                  "layers": LAYERS, "traceability": []}
        report.update(updates)
        return report

    def _ledger(self, entries: list[dict]) -> None:
        write_json(self.root / ".storyloop/state/telemetry/FEAT-1.json",
                   {"version": 1, "change_id": "FEAT-1", "entries": entries})

    def _meta(self, call: str, role: str, ledger: object = "FEAT-1", status: str = "OK") -> None:
        write_json(self.root / f".storyloop/state/telemetry/calls/{call}/meta.json",
                   {"version": 1, "call": call, "role": role, "ledger": ledger,
                    "status": status, "wall_ms": 3, "sessions": [], "repairs": 0})

    def test_collects_allowlisted_run_anchored_git_and_protected_digest(self):
        self._run(routing={"initial_tier": "standard"}, repairs=[{"attempt": 1}])
        (self.root / "tracked.py").write_text("VALUE    =    1\n", encoding="utf-8")
        (self.root / "path with spaces.txt").write_text("new\n", encoding="utf-8")
        git(self.root, "add", "tracked.py", "path with spaces.txt")
        git(self.root, "commit", "-q", "-m", "candidate")
        evidence_path = self.root / ".storyloop/evidence/FEAT-1/acceptance-result.json"
        write_json(evidence_path, {"status": "PASSED"})
        digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()

        result = collect_evidence(self.root, self.change,
                                  self._report(traceability=[{"evidence_sha256": digest}]))

        self.assertEqual(result["git"]["base"], self.base)
        by_path = {item["path"]: item for item in result["git"]["files"]}
        self.assertTrue(by_path["tracked.py"]["whitespace_only"])
        self.assertEqual(by_path["path with spaces.txt"]["status"], "A")
        self.assertNotIn("answers", result["run"])
        self.assertEqual(result["availability"]["evidence"]["actual_sha256"], digest)

    def test_declared_evidence_path_cannot_escape_the_protected_location(self):
        self._run()
        evidence_path = self.root / ".storyloop/evidence/FEAT-1/acceptance-result.json"
        write_json(evidence_path, {"status": "PASSED"})
        digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        layers = {key: dict(value) for key, value in LAYERS.items()}
        layers["acceptance"] = {"status": "PASSED", "results": [],
                                "evidence_sha256": digest, "evidence_path": "../secret.json"}
        report = self._report(layers=layers)
        result = reconcile(self.root, self.change, report,
                           collect_evidence(self.root, self.change, report))
        self.assertEqual(result["status"], "MISMATCH")
        check = next(item for item in result["checks"] if item["id"] == "protected_evidence")
        self.assertEqual(check["status"], "MISMATCH")

    def test_digest_without_declared_evidence_path_is_not_a_match(self):
        self._run()
        evidence_path = self.root / ".storyloop/evidence/FEAT-1/acceptance-result.json"
        write_json(evidence_path, {"status": "PASSED"})
        digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        layers = {key: dict(value) for key, value in LAYERS.items()}
        layers["acceptance"] = {"status": "PASSED", "results": [],
                                "evidence_sha256": digest}
        report = self._report(layers=layers)
        result = reconcile(self.root, self.change, report,
                           collect_evidence(self.root, self.change, report))
        check = next(item for item in result["checks"] if item["id"] == "protected_evidence")
        self.assertEqual(check["status"], "NOT_AVAILABLE")

    def test_unknown_source_head_does_not_guess_a_parent(self):
        self._run(source_head="not-a-commit")
        evidence = collect_evidence(self.root, self.change, self._report())
        self.assertEqual(evidence["git"]["availability"], "NOT_AVAILABLE")
        self.assertIsNone(evidence["git"]["base"])
        self.assertEqual(evidence["git"]["files"], [])
        self.assertEqual(reconcile(self.root, self.change, self._report(), evidence)["status"],
                         "NOT_AVAILABLE")

    def test_reconciles_native_calls_both_directions_and_pending_product(self):
        self._run()
        self._ledger([{"role": "product", "call": "call-product", "wall_ms": 2},
                      {"role": "review", "call": "call-review", "wall_ms": 3}])
        self._meta("call-product", "product", ledger=None)
        self._meta("call-review", "review")
        evidence = collect_evidence(self.root, self.change, self._report())
        result = reconcile(self.root, self.change, self._report(), evidence)
        self.assertEqual(result["status"], "MATCH")
        self.assertTrue(all(check["status"] in {"MATCH", "PENDING"}
                            for check in result["checks"]))
        self.assertTrue(any("theoretical" in item.lower() for item in result["limitations"]))

    def test_malformed_ledger_entries_are_bounded_unavailable_instead_of_raising(self):
        self._run()
        malformed = [
            [1],
            [{}],
            [{"role": [], "wall_ms": 1}],
            [{"role": "review", "wall_ms": "bad"}],
            [{"role": "review", "wall_ms": 1, "cost_usd": "not-number"}],
        ]
        for entries in malformed:
            with self.subTest(entries=entries):
                self._ledger(entries)
                evidence = collect_evidence(self.root, self.change, self._report())
                self.assertEqual(evidence["availability"]["entries"], "NOT_AVAILABLE")
                self.assertTrue(evidence["entries"]["truncated"]["ledger"])

    def test_reports_missing_duplicate_orphan_and_running_meta_as_mismatch(self):
        self._run()
        self._ledger([{"role": "review", "call": "duplicate", "wall_ms": 1},
                      {"role": "review", "call": "duplicate", "wall_ms": 1},
                      {"role": "implement", "call": "missing", "wall_ms": 1}])
        self._meta("duplicate", "review", status="RUNNING")
        self._meta("orphan", "review")
        evidence = collect_evidence(self.root, self.change, self._report())
        result = reconcile(self.root, self.change, self._report(), evidence)
        self.assertEqual(result["status"], "MISMATCH")
        self.assertTrue(any(c["status"] == "MISMATCH" for c in result["checks"]))

    def test_empty_ledger_keeps_orphan_and_does_not_read_payloads(self):
        self._run()
        self._ledger([])
        folder = self.root / ".storyloop/state/telemetry/calls/legacy"
        self._meta("legacy", "review")
        (folder / "request.json").write_bytes(b"\xff private request")
        (folder / "response.json").write_bytes(b"\xff private response")
        evidence = collect_evidence(self.root, self.change, self._report())
        result = reconcile(self.root, self.change, self._report(), evidence)
        self.assertEqual(result["status"], "MISMATCH")
        self.assertTrue(any("legacy" in item.lower() for item in result["limitations"]))

    def test_bounds_metadata_and_rejects_traversal_and_external_symlinks(self):
        self._run()
        self._ledger([{"role": "review", "call": "../escape", "wall_ms": 1},
                      {"role": "review", "call": "large", "wall_ms": 1},
                      {"role": "review", "call": "external", "wall_ms": 1}])
        large = self.root / ".storyloop/state/telemetry/calls/large/meta.json"
        large.parent.mkdir(parents=True)
        large.write_bytes(b" " * 300_000)
        malformed = self.root / ".storyloop/state/telemetry/calls/malformed/meta.json"
        malformed.parent.mkdir(parents=True)
        malformed.write_text("{not-json", encoding="utf-8")
        outside = self.root.parent / f"{self.root.name}-secret-meta.json"
        outside.write_text('{"secret":"must-not-appear"}', encoding="utf-8")
        link = self.root / ".storyloop/state/telemetry/calls/external"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(outside)
        try:
            evidence = collect_evidence(self.root, self.change, self._report())
        finally:
            outside.unlink(missing_ok=True)
        encoded = json.dumps(evidence)
        self.assertNotIn("must-not-appear", encoded)
        self.assertTrue(evidence["entries"]["truncated"]["calls"])
        self.assertLess(len(encoded), 100_000)


if __name__ == "__main__":
    unittest.main()
