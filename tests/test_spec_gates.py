"""Requirement quality gate before approval, OpenSpec validation in the loop, superseded scenarios."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import sys
import unittest
from unittest.mock import patch

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from storyloop.changes import create_change, load_change
from storyloop.models import StoryLoopError
from storyloop.openspec_docs import validate_with_cli
from storyloop.project_loop import LoopServices, load_run_state, run_change
from storyloop.requirement_checklist import evaluate_checklist, render_checklist
from tests.test_baseline_behavior import repository


def smoke_payload(change_id: str) -> dict:
    """Each change proves RED on its own marker file, so a second change on the same story still fails first."""
    source = (
        "from pathlib import Path\n"
        f"raise SystemExit(0 if Path('modules/orders/{change_id}.py').is_file() else 1)\n"
    )
    return {"files": [{"path": "flow.py", "content": source}],
            "manifest": {"version": 1, "tests": [{
                "id": "SMOKE-1", "criteria": ["AC-1", "AC-2"],
                "command": ["{python}", f"acceptance/changes/{change_id}/flow.py"], "timeout_seconds": 10}]}}
from tests.test_product_spec import rich_product

NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)


def clean_product() -> dict:
    value = rich_product()
    value["questions"] = []
    value["stories"][0]["rules"] = ["THE SYSTEM SHALL reject overlapping reservations"]
    value["stories"][0]["acceptance"].append({"id": "AC-2", "given": "no reservation exists",
                                              "when": "a room is reserved", "then": "the reservation is stored",
                                              "kind": "automated"})
    return value


class ChecklistTests(unittest.TestCase):
    def test_clean_contract_passes_and_is_rendered_with_checked_items(self):
        items = evaluate_checklist(clean_product())
        self.assertFalse([item for item in items if item["level"] == "error"], items)
        text = render_checklist(items)
        self.assertIn("- [x] CHK", text)
        self.assertIn("Completeness", text)

    def test_unresolved_markers_and_open_questions_block(self):
        value = clean_product()
        value["stories"][0]["rules"].append("Bookings expire after [NEEDS CLARIFICATION: Q9]")
        errors = [item for item in evaluate_checklist(value) if item["level"] == "error"]
        self.assertTrue(any("NEEDS CLARIFICATION" in item["text"] for item in errors))

    def test_vague_words_and_missing_priority_are_warnings_not_blockers(self):
        value = clean_product()
        value["stories"][0].pop("priority")
        value["stories"][0]["acceptance"][0]["then"] = "it works fast and is appropriate"
        items = evaluate_checklist(value)
        self.assertFalse([item for item in items if item["level"] == "error"])
        warnings = [item["text"] for item in items if item["level"] == "warning"]
        self.assertTrue(any("priority" in text for text in warnings))
        self.assertTrue(any("vague" in text.lower() for text in warnings))

    def test_approval_refuses_blocking_items_and_materializes_the_checklist(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            bad = clean_product()
            bad["stories"][0]["rules"].append("Depends on [NEEDS CLARIFICATION: Q3]")
            state = create_change(root, "booking", bad, now=lambda: NOW)
            with self.assertRaisesRegex(StoryLoopError, "checklist"):
                approve_change(root, state["change_id"], now=lambda: NOW)
            draft = root / ".storyloop/drafts" / state["change_id"] / "checklist.md"
            self.assertIn("- [ ] CHK", draft.read_text(encoding="utf-8"))


class OpenSpecGateTests(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "the fake openspec is a POSIX script; Windows cannot exec it")
    def test_missing_cli_is_skipped_and_invalid_output_blocks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch("storyloop.openspec_docs.shutil.which", return_value=None):
                self.assertEqual(validate_with_cli(root, "change", "X")["status"], "skipped")
            fake = root / "openspec-fake"
            fake.write_text("#!/bin/sh\necho '{\"items\":[{\"id\":\"X\",\"type\":\"change\",\"valid\":false,"
                            "\"issues\":[{\"level\":\"ERROR\",\"message\":\"missing scenario\"}]}]}'\n",
                            encoding="utf-8")
            fake.chmod(0o755)
            with patch("storyloop.openspec_docs.shutil.which", return_value=str(fake)), \
                    self.assertRaisesRegex(StoryLoopError, "missing scenario"):
                validate_with_cli(root, "change", "X")


class SupersededTests(unittest.TestCase):
    def _run(self, root: Path, value: dict) -> str:
        state = create_change(root, "booking", value, now=lambda: NOW)
        change_id = state["change_id"]
        approve_change(root, change_id, now=lambda: NOW)

        def implement(project, context):
            target = project / "modules/orders" / f"{change_id}.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("VALUE = 1\n", encoding="utf-8")
            return {"summary": "done", "questions": []}

        services = LoopServices(generate_smoke=lambda p, s: smoke_payload(change_id), implement=implement,
                                review=lambda p, c: {"verdict": "pass", "summary": "ok", "findings": [],
                                                     "questions": []})
        result = run_change(root, change_id, services)
        self.assertEqual(result["status"], "PASSED", result)
        return change_id

    def test_modifying_a_requirement_marks_the_old_scenarios_superseded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root, {"test_commands": []})
            first = self._run(root, clean_product())
            second_value = clean_product()
            second_value["stories"][0]["acceptance"][0]["then"] = "reject it with a clear reason"
            second = self._run(root, second_value)
            self.assertNotEqual(first, second)
            trace = json.loads((root / "openspec/changes" / first / "traceability.json").read_text(encoding="utf-8"))
            superseded = {item["criterion_id"]: item.get("superseded_by") for item in trace["criteria"]}
            self.assertEqual(superseded, {"AC-1": second, "AC-2": second})
            self.assertEqual(load_run_state(root, second)["superseded"],
                             [{"change_id": first, "story_id": "ST-1", "requirement": "Reserve a room",
                               "superseded": ["AC-1", "AC-2"]}])
            tracked = subprocess.run(["git", "ls-files", "-s", f"openspec/changes/{first}/traceability.json"],
                                     cwd=root, capture_output=True, text=True).stdout
            self.assertTrue(tracked)
            self.assertEqual(subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True,
                                            text=True).stdout.strip(), "")
            main = (root / "openspec/specs/room-booking/spec.md").read_text(encoding="utf-8")
            self.assertEqual(main.count("### Requirement: Reserve a room"), 1)
            self.assertIn(f"_Updated by {second}_", main)


if __name__ == "__main__":
    unittest.main()
