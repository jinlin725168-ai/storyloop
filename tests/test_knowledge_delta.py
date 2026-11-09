"""A passed change may propose knowledge; only exact human-authorized bytes land."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project, load_baseline, verify_baseline
from storyloop.changes import load_change
from storyloop.models import StoryLoopError
from storyloop.project_loop import load_run_state, run_change
from storyloop.storage import load_json, write_json
from tests.lifecycle_cases import observed_services
from tests.routing_support import legacy_create_change
from tests.support import product
from tests.test_baseline_behavior import git, repository


class KnowledgeDeltaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        repository(self.root)
        target = self.root / "openspec/foundation/architecture.md"
        target.parent.mkdir(parents=True)
        target.write_text("# Architecture\nold fact\n", encoding="utf-8")
        git(self.root, "add", "openspec/foundation/architecture.md")
        git(self.root, "commit", "-qm", "foundation")
        bootstrap_project(self.root, {"test_commands": [], "max_attempts": 2})
        self.change_id = legacy_create_change(self.root, "orders", product())["change_id"]
        approve_change(self.root, self.change_id)

    def passed(self):
        result = run_change(self.root, self.change_id, observed_services(self.change_id, "success", []))
        self.assertEqual(result["status"], "PASSED")

    def response(self):
        return {"updates": [{"path": "openspec/foundation/architecture.md", "source": "pom.xml:1",
                            "claim": "old fact", "correction": "new fact",
                            "content": "# Architecture\nnew fact\n"}]}

    def propose(self, response=None):
        from storyloop.knowledge_delta import propose_knowledge
        def answer(project, role, payload):
            self.assertEqual(role, "review")
            self.assertEqual(payload["mode"], "knowledge-propose")
            self.assertEqual(payload["change"]["change_id"], self.change_id)
            self.assertIn("acceptance_evidence", payload)
            return self.response() if response is None else response
        with patch("storyloop.knowledge_delta.invoke_project_agent", side_effect=answer):
            return propose_knowledge(self.root, self.change_id)

    def test_non_passed_rejected_and_proposal_is_read_only(self):
        with self.assertRaises(StoryLoopError):
            self.propose()
        self.passed()
        before = git(self.root, "rev-parse", "HEAD")
        proposal = self.propose()
        self.assertEqual(proposal["status"], "KNOWLEDGE_PROPOSED")
        self.assertIn("@@", proposal["updates"][0]["diff"])
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), before)
        self.assertEqual((self.root / "openspec/foundation/architecture.md").read_text(),
                         "# Architecture\nold fact\n")

    def test_exact_authorization_updates_only_target_and_replay_is_idempotent(self):
        from storyloop.knowledge_delta import authorize_knowledge
        self.passed()
        proposal = self.propose()
        path = "openspec/foundation/architecture.md"
        first = authorize_knowledge(self.root, proposal["proposal_id"], [path])
        self.assertEqual(first["status"], "KNOWLEDGE_APPLIED")
        self.assertEqual((self.root / path).read_text(), "# Architecture\nnew fact\n")
        self.assertEqual(git(self.root, "diff-tree", "--no-commit-id", "--name-only", "-r",
                             first["commit"]), path)
        self.assertEqual(load_baseline(self.root)["source_head"], first["commit"])
        self.assertEqual(verify_baseline(self.root)["status"], "BASELINE_VERIFIED")
        self.assertEqual(authorize_knowledge(self.root, proposal["proposal_id"], [path]), first)
        events = load_run_state(self.root, self.change_id)["intervention_events"]
        self.assertEqual(sum(e["kind"] == "knowledge_approval" for e in events), 1)

    def test_reject_and_unsafe_paths_do_not_change_the_change(self):
        from storyloop.knowledge_delta import authorize_knowledge
        self.passed()
        proposal = self.propose()
        for path in ("../outside.md", "openspec/foundation/missing.md"):
            with self.subTest(path=path), self.assertRaises(StoryLoopError):
                authorize_knowledge(self.root, proposal["proposal_id"], [path])
        self.assertEqual(authorize_knowledge(self.root, proposal["proposal_id"], [])["status"],
                         "KNOWLEDGE_REJECTED")
        self.assertEqual(load_change(self.root, self.change_id)["status"], "PASSED")

    def test_workspace_drift_blocks_authorization(self):
        from storyloop.knowledge_delta import authorize_knowledge
        self.passed()
        proposal = self.propose()
        (self.root / "pom.xml").write_text("drift\n", encoding="utf-8")
        with self.assertRaises(StoryLoopError):
            authorize_knowledge(self.root, proposal["proposal_id"],
                                ["openspec/foundation/architecture.md"])

    def test_modified_proposal_diff_and_linked_target_are_rejected(self):
        from storyloop.knowledge_delta import authorize_knowledge
        self.passed()
        proposal = self.propose()
        path = self.root / ".storyloop/state/knowledge/proposals" / (proposal["proposal_id"] + ".json")
        saved = load_json(path)
        saved["updates"][0]["diff"] = "@@ forged @@"
        write_json(path, saved)
        with self.assertRaises(StoryLoopError):
            authorize_knowledge(self.root, proposal["proposal_id"],
                                ["openspec/foundation/architecture.md"])
        saved["updates"][0]["diff"] = proposal["updates"][0]["diff"]
        write_json(path, saved)
        target = self.root / "openspec/foundation/architecture.md"
        target.unlink()
        target.symlink_to(self.root / "pom.xml")
        with self.assertRaises(StoryLoopError):
            authorize_knowledge(self.root, proposal["proposal_id"],
                                ["openspec/foundation/architecture.md"])

    def test_existing_errata_is_linked_instead_of_repeated(self):
        from storyloop.drafts import create_draft, set_draft_status
        self.passed()
        draft = create_draft(self.root, "errata", self.change_id, "review",
                             {"document": "openspec/foundation/architecture.md", "claim": "old fact",
                              "correction": "new fact", "evidence": ["pom.xml:1"]}, None)
        set_draft_status(self.root, "errata/" + draft, "approved")
        result = self.propose()
        self.assertEqual(result["status"], "NO_KNOWLEDGE_DELTA")
        self.assertEqual(result["errata_links"], ["errata/" + draft])
        set_draft_status(self.root, "errata/" + draft, "rejected", "not a correction")
        self.assertEqual(self.propose()["status"], "KNOWLEDGE_PROPOSED")

    def test_cli_has_both_commands_and_empty_paths_mean_reject(self):
        from storyloop.cli import parser
        from storyloop.role_schemas import schema_for
        proposed = parser().parse_args(["knowledge-propose", str(self.root), "--change", self.change_id])
        rejected = parser().parse_args(["authorize-knowledge", str(self.root),
                                        "--proposal", "K-FEAT-test", "--paths"])
        self.assertEqual(proposed.change, self.change_id)
        self.assertEqual(rejected.paths, [])
        self.assertEqual(set(schema_for("review", {"mode": "knowledge-propose"})["properties"]),
                         {"updates"})

    def test_replay_finishes_commit_after_interrupted_baseline_update(self):
        from storyloop.knowledge_delta import authorize_knowledge
        self.passed()
        proposal = self.propose()
        path = "openspec/foundation/architecture.md"
        with patch("storyloop.knowledge_delta._finalize", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                authorize_knowledge(self.root, proposal["proposal_id"], [path])
        committed = git(self.root, "rev-parse", "HEAD")
        result = authorize_knowledge(self.root, proposal["proposal_id"], [path])
        self.assertEqual(result["commit"], committed)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), committed)
        self.assertEqual(verify_baseline(self.root)["status"], "BASELINE_VERIFIED")

    def test_empty_proposal_needs_no_authorization(self):
        self.passed()
        self.assertEqual(self.propose({"updates": []})["status"], "NO_KNOWLEDGE_DELTA")

    def test_existing_foundation_document_can_grow_past_short_text_limit(self):
        self.passed()
        answer = self.response()
        answer["updates"][0]["content"] += "new fact\n" + ("x" * 21_000) + "\n"
        self.assertEqual(self.propose(answer)["status"], "KNOWLEDGE_PROPOSED")
