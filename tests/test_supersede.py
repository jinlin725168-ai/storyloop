"""Contract supersession keeps both runs auditable and linked (2a section 4)."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.changes import load_change, revise_change
from storyloop.models import StoryLoopError
from storyloop.cli import run_with_answers
from storyloop.project_loop import LoopServices, load_run_state, run_change
from storyloop.supersede import supersede_change
from tests.support import product
from tests.test_baseline_behavior import git
from tests.test_failure_routing import approved, failing_review, implementer
from tests.test_project_loop import smoke_payload


def contract_stop(root: Path) -> str:
    change_id = approved(root)
    finding = {"message": "AC-1 contradicts kept behaviour", "target": "contract",
               "criterion_id": "AC-1", "location": "modules/orders/service.py:1"}
    result = run_change(root, change_id, LoopServices(
        generate_smoke=lambda _p, _s: smoke_payload(change_id), implement=implementer([]),
        review=failing_review(finding)))
    assert result["status"] == "NEEDS_SPEC_FIX", result
    return change_id


def revised(_request: dict) -> dict:
    value = product()
    value["goal"] = "Reserve a meeting room, revised"
    return value


class SupersedeTests(unittest.TestCase):
    def test_empty_overrule_is_forwarded_for_validation(self):
        seen = []
        run_with_answers(Path("."), "FEAT-1", False,
                         runner=lambda _p, _c, **kwargs: seen.append(kwargs) or {"status": "PASSED"},
                         overrule="")
        self.assertEqual(seen, [{"overrule": ""}])

    def test_contract_stop_reopens_as_linked_draft(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old_id = contract_stop(root)
            seen = []
            new = supersede_change(root, old_id, "revise AC-1",
                                   lambda request: seen.append(request) or revised(request))
            self.assertEqual(new["status"], "DRAFT")
            self.assertEqual(new["supersedes"]["change_id"], old_id)
            self.assertEqual(seen[0]["supersede"]["blocker"],
                             "AC-1 contradicts kept behaviour")
            self.assertEqual(seen[0]["supersede"]["facts"],
                             load_run_state(root, old_id).get("facts", []))
            old = load_change(root, old_id)
            self.assertEqual((old["status"], old["superseded_by"]),
                             ("SUPERSEDED", new["change_id"]))
            self.assertEqual(load_run_state(root, old_id)["status"], "SUPERSEDED")

    def test_reviser_failure_does_not_void_old_run(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old_id = contract_stop(root)
            before_change = load_change(root, old_id)["status"]
            before_run = load_run_state(root, old_id)["status"]
            with self.assertRaises(RuntimeError):
                supersede_change(root, old_id, "revise",
                                 lambda _request: (_ for _ in ()).throw(RuntimeError("agent failed")))
            self.assertEqual(load_change(root, old_id)["status"], before_change)
            self.assertEqual(load_run_state(root, old_id)["status"], before_run)

    def test_retry_returns_same_new_change(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old_id = contract_stop(root)
            first = supersede_change(root, old_id, "revise", revised)
            calls = []
            second = supersede_change(root, old_id, "revise",
                                      lambda request: calls.append(request) or revised(request))
            self.assertEqual(second["change_id"], first["change_id"])
            self.assertEqual(calls, [])

    def test_retry_rejects_a_different_reason_even_when_message_matches(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root, max_attempts=1)
            run_change(root, change_id, LoopServices(
                generate_smoke=lambda _p, _s: smoke_payload(change_id), implement=implementer([]),
                review=failing_review()))
            supersede_change(root, change_id, "revise", revised, reason="reason A")
            with self.assertRaisesRegex(StoryLoopError, "different revision"):
                supersede_change(root, change_id, "revise", revised, reason="reason B")

    def test_retry_rebuilds_preview_after_interrupted_child_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old_id = contract_stop(root)
            with patch("storyloop.changes.write_change_preview", side_effect=OSError("disk interruption")):
                with self.assertRaises(OSError):
                    supersede_change(root, old_id, "revise", revised)
            children = [path for path in (root / ".storyloop/state/changes").glob("*.json")
                        if path.stem != old_id]
            self.assertEqual(len(children), 1)
            recovered = supersede_change(root, old_id, "revise", revised)
            proposal = root / ".storyloop/drafts" / recovered["change_id"] / "proposal.md"
            self.assertTrue(proposal.is_file())
            self.assertEqual(load_change(root, old_id)["superseded_by"], recovered["change_id"])

    def test_non_contract_stop_needs_reason_and_passed_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root, max_attempts=1)
            run_change(root, change_id, LoopServices(
                generate_smoke=lambda _p, _s: smoke_payload(change_id), implement=implementer([]),
                review=failing_review()))
            with self.assertRaises(StoryLoopError):
                supersede_change(root, change_id, "revise", revised)
            new = supersede_change(root, change_id, "revise", revised,
                                   reason="criterion cannot be implemented")
            self.assertEqual(new["supersedes"]["reason"], "criterion cannot be implemented")

    def test_passed_change_cannot_be_superseded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root)
            run_change(root, change_id, LoopServices(
                generate_smoke=lambda _p, _s: smoke_payload(change_id), implement=implementer([]),
                review=lambda _p, _c: {"verdict": "pass", "summary": "ok",
                                       "findings": [], "questions": []}))
            with self.assertRaises(StoryLoopError):
                supersede_change(root, change_id, "too late", revised, reason="new contract")

    def test_draft_revision_preserves_supersedes_link(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old_id = contract_stop(root)
            new = supersede_change(root, old_id, "revise", revised)
            updated = revise_change(root, new["change_id"], revised({}), "clarify")
            self.assertEqual(updated["supersedes"], new["supersedes"])

    def test_start_run_source_branch_precondition(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old_id = contract_stop(root)
            new = supersede_change(root, old_id, "revise", revised)
            from storyloop.project_loop import _start_run
            # Isolate the branch precondition; the new routed draft has not passed intake yet.
            with self.assertRaises(StoryLoopError) as caught:
                _start_run(root, new, None)
            self.assertIn("git checkout", str(caught.exception))
            self.assertEqual(git(root, "branch", "--show-current"), f"feature/{old_id}")

    def test_invalid_optional_link_shape_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old_id = contract_stop(root)
            new = supersede_change(root, old_id, "revise", revised)
            path = root / ".storyloop/state/changes" / f"{new['change_id']}.json"
            import json
            state = json.loads(path.read_text(encoding="utf-8"))
            state["supersedes"]["unknown"] = "bad"
            path.write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaises(StoryLoopError):
                load_change(root, new["change_id"])


if __name__ == "__main__":
    unittest.main()
