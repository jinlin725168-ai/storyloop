"""Semantic-invalid role answers get one fresh, visible repair call."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.baseline import bootstrap_project
from storyloop.cli import dispatch, parser
from storyloop.call_log import CALLS_ROOT
from storyloop.change_log import change_log, render_log
from storyloop.changes import create_change
from storyloop.models import StoryLoopError, validate_product
from storyloop.project_agents import instructions_for, invoke_project_agent
from storyloop.project_loop import load_run_state, run_change
from storyloop.role_schemas import normalize, schema_errors, schema_for
from storyloop.semantic_repair import answer_with_repair
from storyloop.smoke import validate_suite
from storyloop.storage import write_json
from tests.support import product
from tests.test_baseline_behavior import repository
from tests.test_design_role import PLAN, UNIT, _repo, _services
from tests.test_structured_output import approved_change, passing_review
from tests.test_project_loop import smoke_payload


def semantic_invalid_product() -> dict:
    """Schema-valid, but marks a must-hold regression row as defect reproduction."""
    value = product()
    value["stories"][0]["acceptance"][0].update(
        {"row_type": "regression", "reproduces_defect": True}
    )
    normalized = normalize(value, schema_for("product", {}))
    if schema_errors(normalized, schema_for("product", {})):
        raise AssertionError("fixture must pass the product response schema")
    return value


class AnswerWithRepairTests(unittest.TestCase):
    def test_semantic_failure_is_retried_once_with_the_answer_and_error(self):
        requests = []
        answers = [semantic_invalid_product(), product()]

        def call(request):
            requests.append(request)
            return answers.pop(0)

        repaired = answer_with_repair(call, {"idea": "reserve"}, validate_product)

        self.assertEqual(repaired, product())
        self.assertEqual(requests[1]["previous_response"], semantic_invalid_product())
        self.assertEqual(
            requests[1]["validation_errors"],
            ["A defect reproduction must be a must-be-red row"],
        )

    def test_second_semantic_failure_is_raised_without_a_third_call(self):
        requests = []
        with self.assertRaisesRegex(StoryLoopError, "must-be-red"):
            answer_with_repair(
                lambda request: requests.append(request) or semantic_invalid_product(),
                {},
                validate_product,
            )
        self.assertEqual(len(requests), 2)

    def test_infrastructure_permission_and_call_failures_are_not_retried(self):
        for failure in (
            StoryLoopError("down", code="AGENT_UNAVAILABLE"),
            StoryLoopError("tainted", code="WORKSPACE_TAINTED"),
            StoryLoopError("denied", code="PERMISSION_DENIED"),
            OSError("call failed"),
        ):
            calls = []

            def call(request, error=failure):
                calls.append(request)
                raise error

            with self.subTest(error=str(failure)), self.assertRaises(type(failure)):
                answer_with_repair(call, {}, lambda answer: None)
            self.assertEqual(len(calls), 1)


class SemanticRepairIntegrationTests(unittest.TestCase):
    def test_change_repairs_one_semantically_invalid_product(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            from tests.routing_support import readiness_repository
            from tests.test_intake import assessed_product
            from storyloop.readiness import inspect_readiness, approve_readiness
            readiness_repository(root)
            ready = inspect_readiness(root)
            approve_readiness(root, ready['digest'], 'Local tests')
            answers = [semantic_invalid_product(), assessed_product()]
            with patch("storyloop.cli.invoke_project_agent", side_effect=lambda p, r, payload: answers.pop(0)) as call:
                created = dispatch(parser().parse_args([
                    "change", str(root), "--idea", "增加预订", "--no-interactive",
                ]))
            self.assertEqual(created["status"], "DRAFT")
            self.assertIn("validation_errors", call.call_args_list[1].args[2])

    def test_acceptance_repairs_invalid_manifest_before_writing(self):
        temporary, root, change_id = approved_change()
        calls = []

        def generate(project, request):
            calls.append(request)
            answer = smoke_payload(change_id)
            if len(calls) == 1:
                answer["manifest"]["tests"] = []
            return answer

        def implement(project, context):
            target = project / "modules/orders/service.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("VALUE = 1\n", encoding="utf-8")
            return {"summary": "done", "questions": []}

        from storyloop.project_loop import LoopServices
        try:
            result = run_change(root, change_id, LoopServices(
                generate_smoke=generate, implement=implement, review=passing_review,
            ))
            self.assertEqual(result["status"], "PASSED")
            self.assertIn("Smoke manifest needs tests", calls[1]["validation_errors"])
            self.assertEqual(calls[1]["previous_response"]["manifest"]["tests"], [])
        finally:
            temporary.cleanup()

    def test_design_retries_a_schema_valid_answer_naming_a_missing_file(self):
        temporary, root, change_id = _repo({"unit_commands": [UNIT]})
        requests = []

        def design(project, request):
            requests.append(request)
            touched = ["modules/orders/missing.py"] if len(requests) == 1 else ["modules/orders/service.py"]
            return {"summary": "plan", "touched": touched, "design": PLAN, "questions": []}

        try:
            result = run_change(root, change_id, _services(change_id, design))
            self.assertEqual(result["status"], "PASSED")
            self.assertIn("does not exist", requests[1]["validation_errors"][0])
            self.assertEqual(requests[1]["previous_response"]["touched"], ["modules/orders/missing.py"])
        finally:
            temporary.cleanup()

    def test_design_workspace_taint_stops_before_semantic_retry(self):
        temporary, root, change_id = _repo({"unit_commands": [UNIT]})
        calls = []

        def design(project, request):
            calls.append(request)
            (project / "tainted.txt").write_text("unexpected\n", encoding="utf-8")
            return {"summary": "plan", "touched": ["modules/orders/missing.py"],
                    "design": PLAN, "questions": []}

        try:
            with self.assertRaises(StoryLoopError) as raised:
                run_change(root, change_id, _services(change_id, design))
            self.assertEqual(raised.exception.code, "WORKSPACE_TAINTED")
            self.assertEqual(len(calls), 1)
            saved = load_run_state(root, change_id)
            self.assertEqual(saved["status"], "WORKSPACE_TAINTED")
            self.assertEqual(saved["stop"]["status"], "WORKSPACE_TAINTED")
        finally:
            temporary.cleanup()

    def test_validate_suite_checks_generated_content_without_writing_it(self):
        change = {"product": product()}
        files = [{"path": "test_smoke.py", "content": "raise SystemExit(1)\n"}]
        invalid_manifest = {"version": 1, "tests": []}

        with self.assertRaisesRegex(StoryLoopError, "needs tests"):
            validate_suite(change, files, invalid_manifest)

    def test_repair_payload_is_explained_and_marked_in_the_call_ledger(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            config_path = root / ".storyloop/config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["adapter"] = "manual"
            write_json(config_path, config)
            change_id = create_change(root, "reserve", product())["change_id"]
            payload = {
                "change_id": change_id,
                "idea": "reserve",
                "previous_response": semantic_invalid_product(),
                "validation_errors": ["must-be-red"],
            }

            with self.assertRaisesRegex(StoryLoopError, "Manual input"):
                invoke_project_agent(root, "product", payload)

            metas = sorted((root / CALLS_ROOT).glob("*/meta.json"))
            meta = json.loads(metas[-1].read_text(encoding="utf-8"))
            self.assertIs(meta["semantic_repair"], True)
            self.assertIn("payload.validation_errors", instructions_for(root, "product"))
            log = change_log(root, change_id)
            self.assertIs(log["calls"][-1]["semantic_repair"], True)
            self.assertIn("语义校验修复：带着上一次的校验错误重答", render_log(log))


if __name__ == "__main__":
    unittest.main()
