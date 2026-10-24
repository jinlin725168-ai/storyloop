"""Every change leaves OpenSpec-format artifacts behind: delta spec, design, decisions, living spec."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess
import tempfile
import unittest

from storyloop.models import StoryLoopError
from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from tests.routing_support import legacy_create_change as create_change
from storyloop.openspec_docs import document_change, merge_into_main_spec, render_decisions, render_delta_spec
from storyloop.project_contracts import validate_agent_summary
from storyloop.project_loop import LoopServices, run_change
from tests.test_baseline_behavior import repository
from tests.test_project_loop import smoke_payload
from tests.test_product_spec import rich_product

NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)


class RenderTests(unittest.TestCase):
    def test_delta_spec_marks_added_or_modified_against_the_main_spec(self):
        value = rich_product()
        delta = render_delta_spec(value, "FEAT-1", main_spec=None)
        self.assertIn("## ADDED Requirements", delta)
        self.assertIn("### Requirement: Reserve a room", delta)
        self.assertIn("The system SHALL", delta)
        self.assertIn("#### Scenario: AC-1", delta)
        self.assertIn("- **WHEN** another overlaps", delta)
        self.assertIn("- **THEN** reject it", delta)
        main, replaced = merge_into_main_spec(None, delta, "room-booking", "FEAT-1")
        self.assertEqual(replaced, [])
        self.assertTrue(main.startswith("# room-booking Specification"))
        self.assertIn("## Requirements", main)
        self.assertIn("### Requirement: Reserve a room", main)
        again = render_delta_spec(value, "FEAT-2", main_spec=main)
        self.assertIn("## MODIFIED Requirements", again)
        self.assertNotIn("## ADDED Requirements", again)
        merged, replaced = merge_into_main_spec(main, again, "room-booking", "FEAT-2")
        self.assertEqual(replaced, [{"change_id": "FEAT-1", "story_id": "ST-1", "requirement": "Reserve a room",
                                     "superseded": ["AC-1"]}])
        self.assertEqual(merged.count("### Requirement: Reserve a room"), 1)
        self.assertIn("FEAT-2", merged)

    def test_modified_requirement_carries_over_scenarios_it_does_not_restate(self):
        first = rich_product()
        first["stories"][0]["acceptance"].append({"id": "AC-2", "given": "g", "when": "w", "then": "t",
                                                  "kind": "automated"})
        main, _ = merge_into_main_spec(None, render_delta_spec(first, "FEAT-1", None), "room-booking", "FEAT-1")
        second = rich_product()  # only AC-1 restated
        delta = render_delta_spec(second, "FEAT-2", main)
        self.assertIn("#### Scenario: AC-2", delta)
        self.assertIn("carried over unchanged from FEAT-1", delta)
        merged, replaced = merge_into_main_spec(main, delta, "room-booking", "FEAT-2")
        self.assertEqual(replaced[0]["superseded"], ["AC-1"])
        self.assertEqual(merged.count("#### Scenario: AC-2"), 1)

    def test_decisions_document_lists_every_human_input_in_order(self):
        change = {"change_id": "FEAT-1", "idea": "book rooms", "history": [
            {"revision": 1, "message": "Q1 选 1", "replaced_at": "2026-09-21T01:00:00Z", "digest": "x"}],
            "product": {"clarifications": [{"session": "2026-09-21", "question": "Cancel?", "answer": "Yes"}]}}
        text = render_decisions(change, answers=[{"attempt": 2, "question": "Sort?", "answer": "By code",
                                                  "answered_at": "2026-09-21T02:00:00Z"}],
                                manual=[{"criterion_id": "AC-2", "note": "looked fine",
                                         "accepted_at": "2026-09-21T03:00:00Z"}],
                                resets=[{"at": "2026-09-21T02:30:00Z", "from_status": "WORKSPACE_TAINTED",
                                         "attempt": 1, "reason": "stray build output"}])
        for token in ("book rooms", "Q1 选 1", "Cancel? → Yes", "Sort? → By code", "AC-2", "looked fine",
                      "WORKSPACE_TAINTED", "stray build output"):
            self.assertIn(token, text)

    def test_implement_contract_accepts_optional_design(self):
        validate_agent_summary({"summary": "ok", "questions": [], "design": "## Context\nx"})
        validate_agent_summary({"summary": "ok", "questions": []})


class ArchiveTests(unittest.TestCase):
    def test_passed_change_commits_delta_design_decisions_and_living_spec(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root, {"test_commands": []})
            value = rich_product()
            value["questions"] = []
            state = create_change(root, "orders", value, now=lambda: NOW)
            change_id = state["change_id"]
            approve_change(root, change_id, now=lambda: NOW)

            def implement(project, context):
                target = project / "modules/orders/service.py"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("VALUE = 1\n", encoding="utf-8")
                return {"summary": "done", "questions": [],
                        "design": "## Context\nsingle table\n\n## Decisions\n- Choice: keep it simple"}

            services = LoopServices(generate_smoke=lambda p, s: smoke_payload(change_id), implement=implement,
                                    review=lambda p, c: {"verdict": "pass", "summary": "ok", "findings": [],
                                                         "questions": []})
            done = run_change(root, change_id, services)
            self.assertEqual(done["status"], "PASSED", done)
            tracked = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout.split()
            base = f"openspec/changes/{change_id}"
            for path in (f"{base}/specs/room-booking/spec.md", f"{base}/design.md", f"{base}/decisions.md",
                         "openspec/specs/room-booking/spec.md"):
                self.assertIn(path, tracked, path)
            self.assertIn("keep it simple", (root / base / "design.md").read_text(encoding="utf-8"))
            self.assertIn("Reserve a room", (root / "openspec/specs/room-booking/spec.md").read_text(encoding="utf-8"))
            self.assertEqual(subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True,
                                            text=True).stdout.strip(), "")
            # `document` regenerates the same artifacts for a finished change and can attach a design
            (root / base / "decisions.md").unlink()
            (root / base / "design.md").write_text("stale\n", encoding="utf-8")
            extra = root / ".storyloop/new-design.md"
            extra.write_text("## Context\nrewritten\n", encoding="utf-8")
            with self.assertRaises(StoryLoopError):
                document_change(root, change_id, capability="other-name")  # contract already names one
            result = document_change(root, change_id, design_path=extra)
            self.assertEqual(set(result["written"]), {f"{base}/decisions.md", f"{base}/design.md",
                                                     "openspec/specs/room-booking/spec.md"})
            self.assertIn("rewritten", (root / base / "design.md").read_text(encoding="utf-8"))
            self.assertIn("Reserve a room", (root / base / "decisions.md").read_text(encoding="utf-8")
                          + (root / "openspec/specs/room-booking/spec.md").read_text(encoding="utf-8"))
            self.assertEqual((root / "openspec/specs/room-booking/spec.md").read_text(encoding="utf-8").count(
                "### Requirement: Reserve a room"), 1)


if __name__ == "__main__":
    unittest.main()
