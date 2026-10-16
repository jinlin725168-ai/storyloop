"""A real red acceptance layer still gets evidence-checked attribution review."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.answers import record_answers
from storyloop.models import StoryLoopError
from storyloop.project_loop import LoopServices, load_run_state, run_change
from tests.test_failure_routing import approved, failing_review, oracle_finding
from tests.test_project_loop import smoke_payload


def red_implement(project, context):
    target = project / "modules/orders/service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("VALUE = 0\n", encoding="utf-8")
    return {"summary": "candidate still fails acceptance", "questions": []}


def services(change_id, review):
    return LoopServices(generate_smoke=lambda p, s: smoke_payload(change_id),
                        implement=red_implement, review=review)


class FailedLayerRoutingTests(unittest.TestCase):
    def test_real_red_acceptance_routes_oracle_and_contract_on_first_attempt(self):
        for target, status in (("oracle", "ORACLE_DEFECT"), ("contract", "NEEDS_SPEC_FIX")):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                change_id = approved(root)
                finding = oracle_finding(change_id)
                finding["target"] = target
                seen = []

                def review(project, context):
                    seen.append(deepcopy(context["layers"]))
                    return failing_review(finding)(project, context)

                result = run_change(root, change_id, services(change_id, review))
                self.assertEqual(result["status"], status)
                state = load_run_state(root, change_id)
                self.assertEqual(state["attempt"], 1)
                self.assertEqual(len(state["failures"]), 1)
                failure = state["failures"][0]
                self.assertEqual(failure["acceptance"]["status"], "FAILED")
                self.assertEqual({name: failure[name] for name in seen[0]}, seen[0])
                self.assertEqual(failure["review"]["findings"][0]["target"], target)

    def test_pass_review_cannot_override_red_and_repeated_failure_stops(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root, max_attempts=6)
            result = run_change(root, change_id, services(change_id, lambda p, c: {
                "verdict": "pass", "summary": str(c["attempt"]), "findings": [], "questions": []}))
            self.assertEqual(result["status"], "NO_PROGRESS")
            self.assertIn("acceptance=FAILED", result["stop"]["last_verdict"]["summary"])
            self.assertTrue(all("acceptance=FAILED" in item["summary"]
                                for item in result["stop"]["attempts"]))
            failures = load_run_state(root, change_id)["failures"]
            self.assertEqual(len(failures), 3)
            self.assertEqual(len({item["signature"] for item in failures}), 1)
            self.assertEqual(failures[-1]["mode"], "root_cause")
            self.assertTrue(all(item["review"]["verdict"] == "pass" for item in failures))

    def test_pure_environment_failure_skips_review(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root, max_attempts=1)
            layers = {name: {"status": "SKIPPED", "results": []}
                      for name in ("unit", "integration", "smoke", "checks")}
            layers["acceptance"] = {"status": "ERROR", "results": [
                {"id": "SMOKE-1", "status": "ERROR", "message": "runner unavailable"}]}
            def no_review(p, c):
                self.fail("environment failure must skip reviewer")
            with patch("storyloop.project_loop._run_checks_safely", side_effect=lambda p, c, cfg, b: (layers, b)):
                result = run_change(root, change_id, services(change_id, no_review))
            self.assertEqual(result["status"], "BUDGET_EXHAUSTED")
            self.assertEqual(load_run_state(root, change_id)["failures"][0]["attribution"], "environment")

    def test_mixed_routing_and_overrules_keep_original_layers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root, max_attempts=2)
            contract = {"target": "contract", "message": "contradiction", "criterion_id": "AC-1",
                        "location": "modules/orders/service.py:1"}
            agent = services(change_id, failing_review(oracle_finding(change_id), contract))
            self.assertEqual(run_change(root, change_id, agent)["status"], "NEEDS_SPEC_FIX")
            original = load_run_state(root, change_id)["failures"][0]["acceptance"]
            self.assertEqual(run_change(root, change_id, agent, overrule="contract is valid")["status"], "ORACLE_DEFECT")
            self.assertEqual(run_change(root, change_id, agent, overrule="oracle is valid")["status"], "BUDGET_EXHAUSTED")
            state = load_run_state(root, change_id)
            self.assertEqual(state["failures"][0]["acceptance"], original)
            self.assertTrue(all(f["target"] == "implementation" for f in state["failures"][-1]["review"]["findings"]))

    def test_needs_input_keeps_red_evidence_and_checked_findings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root)
            result = run_change(root, change_id, services(change_id, lambda p, c: {
                "verdict": "needs_input", "summary": "ambiguous", "questions": ["Which outcome?"],
                "findings": [{"target": "oracle", "message": "unsupported"}]}))
            self.assertEqual(result["status"], "NEEDS_INPUT")
            failure = load_run_state(root, change_id)["failures"][0]
            self.assertEqual(failure["acceptance"]["status"], "FAILED")
            self.assertEqual(failure["review"]["findings"][0]["target"], "implementation")

    def test_red_review_is_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root)
            def mutate(project, context):
                (project / "modules/orders/service.py").write_text("VALUE = 1\n", encoding="utf-8")
                return {"verdict": "pass", "summary": "changed", "findings": [], "questions": []}
            with self.assertRaises(StoryLoopError) as caught:
                run_change(root, change_id, services(change_id, mutate))
            self.assertEqual(caught.exception.code, "WORKSPACE_TAINTED")
            self.assertIn("Read-only operation modified", str(caught.exception))
            self.assertEqual(load_run_state(root, change_id)["status"], "WORKSPACE_TAINTED")

    def test_red_review_question_preserves_evidence_but_breaks_failure_streak(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root, max_attempts=6)
            modes = []
            def implement(project, context):
                modes.append((context["attempt"], context["mode"]))
                return red_implement(project, context)
            def review(project, context):
                question = context["attempt"] == 2
                return {"verdict": "needs_input" if question else "fail", "summary": "red",
                        "findings": [], "questions": ["Continue?"] if question else []}
            agent = LoopServices(generate_smoke=lambda p, s: smoke_payload(change_id),
                                 implement=implement, review=review)
            self.assertEqual(run_change(root, change_id, agent)["status"], "NEEDS_INPUT")
            before = load_run_state(root, change_id)["failures"]
            self.assertEqual(before[-1]["acceptance"]["status"], "FAILED")
            record_answers(root, change_id, ["Continue"])
            self.assertEqual(run_change(root, change_id, agent)["status"], "NO_PROGRESS")
            self.assertEqual(modes, [(1, None), (2, None), (3, None),
                                     (4, None), (5, "root_cause")])


if __name__ == "__main__":
    unittest.main()
