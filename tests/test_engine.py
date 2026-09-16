"""End-to-end runner contracts with real child checks and a controlled agent."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tests.support import product, check, config, write
from storyloop.storage import initialize, load_json
from storyloop.models import StoryLoopError
from storyloop.engine import approve, run, accept, status


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        initialize(self.project, "room booking")
        write(self.project / "product.json", product())
        write(self.project / "config.json", config())
        health = check(kind="health")
        health["id"] = "HEALTH"
        acceptance = check()
        acceptance["command"][-1] = ("import json,sys,pathlib; good=pathlib.Path('implemented.txt').exists(); "
            "print(json.dumps({'protocol':'storyloop.check.v1','results':[{'id':'overlap','status':'pass' if good else 'fail','message':'overlap prevention'}]})); "
            "sys.exit(0 if good else 1)")
        write(self.project / "checks.json", {"version": 1, "checks": [health, acceptance]})
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def agent(self, project, role, payload):
        self.calls.append(role)
        if role == "implement":
            (project / "workspace" / "implemented.txt").write_text("real candidate change")
            return {"summary": "Implemented overlap prevention", "questions": []}
        return {"verdict": "pass", "summary": "Acceptance is satisfied", "findings": [], "questions": []}

    def execute(self, agent=None):
        with patch("storyloop.agents.invoke", agent or self.agent):
            return run(self.project)

    def test_approval_required(self):
        with self.assertRaises(StoryLoopError):
            self.execute()

    def test_real_red_then_green_and_independent_qa(self):
        approve(self.project)
        result = self.execute()
        self.assertEqual(result["stories"]["ST-1"]["status"], "PASSED")
        self.assertIn("implement", self.calls)
        self.assertIn("review", self.calls)
        self.assertTrue(result["stories"]["ST-1"]["evidence"])
        self.assertTrue(any(c["outcome"] == "RED" for c in result["stories"]["ST-1"]["baseline"]))

    def test_existing_green_is_not_artificially_broken(self):
        (self.project / "workspace" / "implemented.txt").write_text("already implemented")
        approve(self.project)
        result = self.execute()
        self.assertEqual(result["stories"]["ST-1"]["status"], "PASSED")
        self.assertNotIn("implement", self.calls)

    def test_product_questions_prevent_approval(self):
        value = product()
        value["questions"] = [{"id": "Q1", "text": "Allow adjacent meetings?", "options": ["yes", "no"]}]
        write(self.project / "product.json", value)
        with self.assertRaises(StoryLoopError):
            approve(self.project)

    def test_changed_contract_blocks_run(self):
        approve(self.project)
        (self.project / "qa" / "changed.py").write_text("# changed judge")
        with self.assertRaises(StoryLoopError):
            self.execute()

    def test_agent_cannot_rewrite_the_acceptance_contract(self):
        approve(self.project)
        def agent(project, role, payload):
            response = self.agent(project, role, payload)
            if role == "implement":
                (project / "qa" / "tampered.py").write_text("# unauthorized")
            return response
        result = self.execute(agent)
        self.assertEqual(result["stories"]["ST-1"]["status"], "BLOCKED")

    def test_pass_review_with_findings_is_blocked(self):
        approve(self.project)
        def agent(project, role, payload):
            value = self.agent(project, role, payload)
            if role == "review":
                value["findings"] = [{"criterion_id": "AC-1", "message": "Still broken"}]
            return value
        self.assertEqual(self.execute(agent)["stories"]["ST-1"]["status"], "BLOCKED")

    def test_repeated_failure_budget_survives_another_run(self):
        approve(self.project)
        def agent(project, role, payload):
            self.calls.append(role)
            return {"summary": "No fix", "questions": []}
        first = self.execute(agent)
        attempts = first["stories"]["ST-1"]["attempts"]
        second = self.execute(agent)
        self.assertEqual(second["stories"]["ST-1"]["status"], "FAILED")
        self.assertEqual(second["stories"]["ST-1"]["attempts"], attempts)

    def test_manual_story_stays_pending_until_human_accepts(self):
        value = product()
        value["stories"][0]["acceptance"].append({"id": "AC-2", "kind": "manual", "given": "first visit", "when": "booking", "then": "workflow is understandable"})
        write(self.project / "product.json", value)
        approve(self.project)
        result = self.execute()
        self.assertEqual(result["stories"]["ST-1"]["status"], "AWAITING_ACCEPTANCE")
        accept(self.project, "ST-1", "Walked through booking and understood the conflict message")
        self.assertEqual(status(self.project)["stories"]["ST-1"]["status"], "ACCEPTED")

    def test_changed_workspace_invalidates_acceptance(self):
        approve(self.project)
        self.execute()
        (self.project / "workspace" / "implemented.txt").write_text("changed afterward")
        self.assertEqual(status(self.project)["stories"]["ST-1"]["status"], "STALE")
        with self.assertRaises(StoryLoopError):
            accept(self.project, "ST-1", "Looks fine")

    def test_empty_human_acceptance_is_rejected(self):
        approve(self.project)
        self.execute()
        with self.assertRaises(StoryLoopError):
            accept(self.project, "ST-1", " ")


    def test_missing_evidence_prevents_reuse_and_human_acceptance(self):
        approve(self.project)
        result = self.execute()
        evidence = self.project / result["stories"]["ST-1"]["evidence"][0]
        evidence.unlink()
        self.assertEqual(status(self.project)["stories"]["ST-1"]["status"], "STALE")
        with self.assertRaises(StoryLoopError):
            accept(self.project, "ST-1", "Reviewed")

    def test_review_text_is_redacted_before_persistence(self):
        approve(self.project)
        def agent(project, role, payload):
            result = self.agent(project, role, payload)
            if role == "review":
                result["summary"] = "api_key=synthetic-test-value"
            return result
        self.execute(agent)
        contents = (self.project / ".storyloop/state.json").read_text()
        self.assertNotIn("synthetic-test-value", contents)

    def test_manual_wait_does_not_consume_implementation_attempts(self):
        approve(self.project)
        first = run(self.project)
        self.assertEqual(first["stories"]["ST-1"]["status"], "NEEDS_INPUT")
        self.assertEqual(first["stories"]["ST-1"]["attempts"], 0)
        second = run(self.project)
        self.assertEqual(second["stories"]["ST-1"]["attempts"], 0)

    def test_judge_cannot_modify_runner_state(self):
        values = load_json(self.project / "checks.json")
        values["checks"][1]["command"][-1] = (
            "from pathlib import Path; Path('../.storyloop/state.json').write_text('{}'); "
            + values["checks"][1]["command"][-1])
        write(self.project / "checks.json", values)
        approve(self.project)
        result = self.execute()
        self.assertEqual(result["stories"]["ST-1"]["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
