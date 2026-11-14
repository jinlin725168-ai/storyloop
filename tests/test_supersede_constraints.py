"""Confirmed human decisions survive an explicit supersede."""

import json
from pathlib import Path
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.changes import load_change, revise_change
from storyloop.models import StoryLoopError
from storyloop.product_cli import draft_result, revise_with_agent
from storyloop.product_contract import validate_product
from storyloop.storage import write_json
from storyloop.supersede import supersede_change
from tests.support import product
from tests.test_supersede import contract_stop, revised


class ConstraintResolutionShapeTests(unittest.TestCase):
    def test_invalid_resolution_type_is_a_contract_error(self):
        value = product()
        value["constraint_resolutions"] = [{"constraint_id": "K-1", "resolution": []}]
        with self.assertRaises(StoryLoopError):
            validate_product(value)


class SupersedeConstraintTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.old_id = contract_stop(self.root)

    def _run(self):
        path = self.root / ".storyloop/state/runs" / f"{self.old_id}.json"
        return path, json.loads(path.read_text(encoding="utf-8"))

    def _supersede(self, replacement=None):
        seen = []

        def revise(request):
            seen.append(request)
            return replacement(request) if replacement else revised(request)

        new = supersede_change(self.root, self.old_id, "revise AC-1", revise)
        proposal = (self.root / ".storyloop/drafts" / new["change_id"] / "proposal.md").read_text()
        return new, proposal, seen[0]

    def test_four_human_sources_are_redacted_and_inherited(self):
        old = load_change(self.root, self.old_id)
        old["product"]["clarifications"] = [{"session": "today", "question": "order?",
                                               "answer": "after base token=clarified"}]
        from storyloop.changes import contract_digest
        old["approval"]["contract_digest"] = contract_digest(old["product"])
        write_json(self.root / ".storyloop/state/changes" / f"{self.old_id}.json", old)
        path, run = self._run()
        run["contract_digest"] = old["approval"]["contract_digest"]
        run["planning"] = {"history": [{"reason": "after base token=replanned"}]}
        run["overrules"] = [{"reason": "after base token=overruled"}]
        write_json(path, run)
        write_json(self.root / ".storyloop/state/answers" / f"{self.old_id}.json",
                   {"version": 1, "change_id": self.old_id,
                    "entries": [{"answer": "after base token=answered", "question": "order?", "attempt": 1}]})
        new, proposal, request = self._supersede()
        inherited = new["inherited_constraints"]
        self.assertEqual([item["source"] for item in inherited],
                         ["clarification", "answer", "replan", "overrule"])
        self.assertEqual([item["id"] for item in inherited], ["K-1", "K-2", "K-3", "K-4"])
        self.assertTrue(all(item["origin_change_id"] == self.old_id for item in inherited))
        self.assertTrue(all("[REDACTED]" in item["text"] for item in inherited))
        self.assertIn("## 沿用的已确认约束", proposal)
        self.assertIn("- K-1（来源：clarification）：after base token=[REDACTED]", proposal)
        self.assertEqual(request["supersede"]["inherited_constraints"], inherited)
        for marker in ("clarified", "answered", "replanned", "overruled"):
            self.assertNotIn(marker, proposal + json.dumps(draft_result(self.root, new)))

    def test_missing_resolution_blocks_with_constraint_id(self):
        path, run = self._run()
        run["overrules"] = [{"reason": "after base"}]
        write_json(path, run)
        new, proposal, _ = self._supersede()
        self.assertIn("## 沿用的已确认约束", proposal)
        blockers = draft_result(self.root, new)["blocking_items"]
        self.assertTrue(any(item.get("constraint_id") == "K-1" for item in blockers))
        with self.assertRaises(StoryLoopError):
            approve_change(self.root, new["change_id"])

    def test_conflict_resolution_requires_referenced_question(self):
        path, run = self._run()
        run["overrules"] = [{"reason": "after base"}]
        write_json(path, run)

        def conflicted(_request):
            value = product()
            value["constraint_resolutions"] = [
                {"constraint_id": "K-1", "resolution": "conflict", "question_id": "Q2"}]
            value["questions"] = [{"id": "Q2", "text": "K-1 conflicts?", "options": ["keep", "change"]}]
            return value

        new, _, _ = self._supersede(conflicted)
        self.assertEqual(new["product"]["constraint_resolutions"][0]["question_id"], "Q2")
        self.assertFalse(any(item.get("constraint_id") == "K-1" for item in
                             draft_result(self.root, new)["blocking_items"]))
        self.assertEqual(new["status"], "DRAFT")

    def test_criteria_diff_is_current_after_revision(self):
        old = load_change(self.root, self.old_id)
        old_criterion = old["product"]["stories"][0]["acceptance"][0]

        def replacement(_request):
            value = product()
            value["stories"][0]["acceptance"].append(
                {**old_criterion, "id": "AC-NEW"})
            return value

        new, proposal, _ = self._supersede(replacement)
        self.assertEqual(new["criteria_diff"],
                         {"added": ["AC-NEW"], "removed": [], "changed": []})
        self.assertIn("新增：AC-NEW", proposal)
        self.assertIn("删除：无", proposal)
        self.assertIn("改动：无", proposal)
        value = product()
        value["stories"][0]["acceptance"][0]["then"] = "new outcome"
        revised_state = revise_change(self.root, new["change_id"], value, "change criterion")
        self.assertEqual(draft_result(self.root, revised_state)["criteria_diff"],
                         {"added": [], "removed": [], "changed": ["AC-1"]})

    def test_old_change_status_and_new_draft_link_survive(self):
        new, _, _ = self._supersede()
        self.assertEqual(load_change(self.root, self.old_id)["status"], "SUPERSEDED")
        self.assertEqual(new["status"], "DRAFT")
        self.assertEqual(new["supersedes"]["change_id"], self.old_id)

    def test_removed_criterion_is_listed(self):
        old = load_change(self.root, self.old_id)
        old["product"]["stories"][0]["acceptance"].append(
            {**old["product"]["stories"][0]["acceptance"][0], "id": "AC-REMOVE"})
        from storyloop.changes import contract_digest
        old["approval"]["contract_digest"] = contract_digest(old["product"])
        write_json(self.root / ".storyloop/state/changes" / f"{self.old_id}.json", old)
        path, run = self._run()
        run["contract_digest"] = old["approval"]["contract_digest"]
        write_json(path, run)
        new, proposal, _ = self._supersede()
        self.assertEqual(new["criteria_diff"]["removed"], ["AC-REMOVE"])
        self.assertIn("删除：AC-REMOVE", proposal)

    def test_unreferenced_conflict_question_remains_blocked(self):
        path, run = self._run()
        run["overrules"] = [{"reason": "after base"}]
        write_json(path, run)

        def conflicted(_request):
            value = product()
            value["constraint_resolutions"] = [
                {"constraint_id": "K-1", "resolution": "conflict", "question_id": "Q2"}]
            value["questions"] = [{"id": "Q2", "text": "Should we change order?",
                                   "options": ["keep", "change"]}]
            return value

        new, _, _ = self._supersede(conflicted)
        self.assertTrue(any(item.get("constraint_id") == "K-1" for item in
                            draft_result(self.root, new)["blocking_items"]))

    def test_run_evidence_and_calls_are_not_copied_to_public_draft(self):
        path, run = self._run()
        run.setdefault("stop", {})["evidence"] = [{"message": "EVIDENCE_MARKER"}]
        run["calls"] = [{"message": "CALL_MARKER"}]
        write_json(path, run)

        def echo(request):
            value = product()
            value["goal"] += " " + " ".join(
                marker for marker in ("EVIDENCE_MARKER", "CALL_MARKER")
                if marker in json.dumps(request))
            return value

        new, proposal, _ = self._supersede(echo)
        public = proposal + json.dumps(draft_result(self.root, new))
        self.assertNotIn("EVIDENCE_MARKER", public)
        self.assertNotIn("CALL_MARKER", public)

    def test_reviser_echo_of_old_secret_is_redacted_before_persisting(self):
        old = load_change(self.root, self.old_id)
        old["product"]["clarifications"] = [{"session": "today", "question": "order?",
                                               "answer": "after base token=old-secret"}]
        from storyloop.changes import contract_digest
        old["approval"]["contract_digest"] = contract_digest(old["product"])
        write_json(self.root / ".storyloop/state/changes" / f"{self.old_id}.json", old)
        path, run = self._run()
        run["contract_digest"] = old["approval"]["contract_digest"]
        write_json(path, run)

        def echo(request):
            value = product()
            value["goal"] += " " + request["current_product"]["clarifications"][0]["answer"]
            value["constraint_resolutions"] = [
                {"constraint_id": "K-1", "resolution": "keep"}]
            return value

        new, proposal, _ = self._supersede(echo)
        self.assertNotIn("old-secret", proposal + json.dumps(draft_result(self.root, new)))

    def test_followup_revision_receives_inherited_constraints(self):
        path, run = self._run()
        run["overrules"] = [{"reason": "after base"}]
        write_json(path, run)
        new, _, _ = self._supersede()
        seen = []

        def answer(_project, payload):
            seen.append(payload)
            value = product()
            value["constraint_resolutions"] = [
                {"constraint_id": "K-1", "resolution": "keep"}]
            return value

        revise_with_agent(self.root, new["change_id"], "confirm scope", [], answer)
        self.assertEqual(seen[0]["inherited_constraints"], new["inherited_constraints"])


if __name__ == "__main__":
    unittest.main()
