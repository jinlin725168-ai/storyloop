"""Agent-stated facts retain only safe, real source locations (2a Q7)."""

from pathlib import Path
import tempfile
import unittest

from storyloop.facts import MAX_FACTS, merge_facts
from storyloop.models import StoryLoopError
from storyloop.project_agents import conform
from storyloop.project_contracts import validate_agent_summary, validate_review
from storyloop.project_loop import LoopServices, load_run_state, run_change
from storyloop.role_schemas import schema_for
from tests.test_characterization import GREEN_TEST, UNIT, _repo as characterization_repo
from tests.test_design_role import _repo, _services
from tests.test_failure_routing import approved
from tests.test_baseline_behavior import git
from tests.test_project_loop import smoke_payload


class MergeFactsTests(unittest.TestCase):
    def test_only_facts_with_real_workspace_lines_are_kept_and_duplicates_merge(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "app.py").write_text("x = 1\n", encoding="utf-8")
            outside = root.parent / "outside-fact.py"
            outside.write_text("secret = 1\n", encoding="utf-8")
            self.addCleanup(outside.unlink, missing_ok=True)
            state = {"attempt": 1, "contract_commit": None}

            merge_facts(root, state, [
                {"statement": " x is one ", "location": "app.py:1"},
                {"statement": "x is one", "location": "app.py:1"},
                {"statement": "line is absent", "location": "app.py:2"},
                {"statement": "outside", "location": "../outside-fact.py:1"},
                {"statement": "missing", "location": "missing.py:1"},
                {"statement": "", "location": "app.py:1"},
                "not an object",
            ], "implement")

            self.assertEqual(state["facts"], [{
                "statement": "x is one", "location": "app.py:1",
                "role": "implement", "attempt": 1,
            }])

    def test_duplicate_key_is_statement_and_file_not_line(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "app.py").write_text("one\ntwo\n", encoding="utf-8")
            state = {"attempt": 2}
            merge_facts(root, state, [
                {"statement": "same", "location": "app.py:1"},
                {"statement": "same", "location": "app.py:2"},
            ], "review")
            self.assertEqual(len(state["facts"]), 1)

    def test_the_record_keeps_the_latest_fifty(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "app.py").write_text("x = 1\n", encoding="utf-8")
            state = {"attempt": 1}
            merge_facts(root, state, [
                {"statement": f"fact {number}", "location": "app.py:1"}
                for number in range(MAX_FACTS + 5)
            ], "review")
            self.assertEqual(len(state["facts"]), 50)
            self.assertEqual(state["facts"][0]["statement"], "fact 5")
            self.assertEqual(state["facts"][-1]["statement"], "fact 54")


class FactsContractsTests(unittest.TestCase):
    def test_role_schemas_accept_facts_and_keep_the_field_optional_for_old_adapters(self):
        implement = {"summary": "done", "questions": [], "design": None}
        normalized, errors = conform(implement, schema_for("implement", {}))
        self.assertEqual(errors, [])
        self.assertNotIn("facts", normalized)

        review = {"verdict": "pass", "summary": "ok", "findings": [], "questions": [],
                  "facts": [{"statement": "x is one", "location": "app.py:1"}]}
        _, errors = conform(review, schema_for("review", {}))
        self.assertEqual(errors, [])

    def test_runtime_contracts_allow_facts(self):
        validate_agent_summary({"summary": "done", "questions": [], "facts": []})
        verdict = validate_review({"verdict": "pass", "summary": "ok", "findings": [],
                                   "questions": [], "facts": []})
        self.assertEqual(verdict, "pass")


class FactsDesignFlowTests(unittest.TestCase):
    def test_design_facts_are_saved_even_when_questions_pause_the_run(self):
        temporary, root, change_id = _repo({"unit_commands": []})

        def design(project, context):
            self.assertEqual(context["facts"], [])
            return {
                "summary": "need a decision", "touched": [], "design": "", "questions": ["Which API?"],
                "facts": [{"statement": "service starts at zero",
                           "location": "modules/orders/service.py:1"}],
            }

        try:
            result = run_change(root, change_id, _services(change_id, design))
            self.assertEqual(result["status"], "NEEDS_INPUT")
            self.assertEqual(load_run_state(root, change_id)["facts"][0]["statement"],
                             "service starts at zero")
        finally:
            temporary.cleanup()

    def test_design_facts_reach_implement_and_review(self):
        temporary, root, change_id = _repo({"unit_commands": []})
        seen: dict = {}

        def design(project, context):
            return {"summary": "plan", "touched": [], "design": "## Plan\n", "questions": [],
                    "facts": [{"statement": "service starts at zero",
                               "location": "modules/orders/service.py:1"}]}

        def implement(project, context):
            self.assertEqual(context["facts"][0]["role"], "design")
            (project / "modules/orders/service.py").write_text("VALUE = 1\n", encoding="utf-8")
            return {"summary": "done", "questions": [], "design": "## Tests First\ncovered\n"}

        try:
            result = run_change(root, change_id, _services(
                change_id, design, implement=implement, seen=seen))
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(seen["review"][0]["facts"][0]["statement"],
                             "service starts at zero")
        finally:
            temporary.cleanup()


class FactsAttemptFlowTests(unittest.TestCase):
    def test_first_attempt_fact_reaches_review_second_attempt_and_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root, max_attempts=2)
            implement_contexts = []
            review_contexts = []

            def implement(project, context):
                implement_contexts.append(context)
                target = project / "modules/orders/service.py"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("VALUE = 1\n", encoding="utf-8")
                facts = ([{"statement": "service exposes VALUE",
                           "location": "modules/orders/service.py:1"}]
                         if context["attempt"] == 1 else [])
                return {"summary": "done", "questions": [], "facts": facts}

            def review(project, context):
                review_contexts.append(context)
                facts = ([{"statement": "VALUE remains one",
                           "location": "modules/orders/service.py:1"}]
                         if context["attempt"] == 2 else [])
                return {"verdict": "fail", "summary": "retry", "questions": [], "facts": facts,
                        "findings": [{"message": "not done", "target": "implementation"}]}

            result = run_change(root, change_id, LoopServices(
                generate_smoke=lambda project, state: smoke_payload(change_id),
                implement=implement, review=review))

            self.assertEqual(implement_contexts[0]["facts"], [])
            self.assertEqual(implement_contexts[1]["facts"][0]["statement"], "service exposes VALUE")
            self.assertEqual(review_contexts[0]["facts"][0]["role"], "implement")
            self.assertEqual([fact["role"] for fact in result["stop"]["facts"]],
                             ["implement", "review"])
            self.assertEqual(len(load_run_state(root, change_id)["facts"]), 2)


class OverruleValidationTests(unittest.TestCase):
    def test_blank_overrule_is_rejected_before_creating_a_feature_branch(self):
        for reason in ("", "   "):
            with self.subTest(reason=repr(reason)), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                change_id = approved(root)
                before = git(root, "branch", "--list")
                with self.assertRaisesRegex(StoryLoopError, "overrule reason must be nonempty text"):
                    run_change(root, change_id, LoopServices(
                        generate_smoke=lambda project, state: smoke_payload(change_id),
                        implement=lambda project, context: {"summary": "done", "questions": []},
                        review=lambda project, context: {
                            "verdict": "pass", "summary": "ok", "findings": [], "questions": []}),
                        overrule=reason)
                self.assertEqual(git(root, "branch", "--list"), before)


class CharacterizationStopPhaseTests(unittest.TestCase):
    def test_illegal_characterization_write_stops_in_prepare(self):
        temporary, root, change_id = characterization_repo(
            {"unit_commands": [UNIT], "test_roots": ["tests"]})

        def leaks(project, context):
            (project / "modules/orders/service.py").write_text("VALUE = 2\n", encoding="utf-8")
            return {"summary": "edited code", "touched": [], "tests": [], "questions": []}

        try:
            result = run_change(root, change_id, LoopServices(
                generate_smoke=lambda project, state: smoke_payload(change_id),
                implement=lambda project, context: self.fail("implementation must not run"),
                review=lambda project, context: self.fail("review must not run"),
                characterize=leaks,
            ))
            self.assertEqual(result["status"], "BOUNDARY_VIOLATION")
            self.assertEqual(result["stop"]["phase"], "prepare")
        finally:
            temporary.cleanup()

    def test_boundary_found_while_resuming_characterization_stops_in_prepare(self):
        temporary, root, change_id = characterization_repo(
            {"unit_commands": [UNIT], "test_roots": ["tests"]})

        def red(project, context):
            path = project / "tests/test_characterize_service.py"
            path.write_text(GREEN_TEST.replace("assertIn", "assertNotIn"), encoding="utf-8")
            return {"summary": "wrong lock", "touched": ["modules/orders/service.py"],
                    "tests": ["tests/test_characterize_service.py"], "questions": []}

        services = LoopServices(
            generate_smoke=lambda project, state: smoke_payload(change_id),
            implement=lambda project, context: self.fail("implementation must not run"),
            review=lambda project, context: self.fail("review must not run"),
            characterize=red,
        )
        try:
            first = run_change(root, change_id, services)
            self.assertEqual(first["status"], "CHARACTERIZATION_FAILED")
            (root / ".github/workflows/characterization.yml").write_text(
                "name: leaked\n", encoding="utf-8")

            result = run_change(root, change_id, services)
            self.assertEqual(result["status"], "BASE_CHANGE_REQUIRED")
            self.assertEqual(result["stop"]["phase"], "prepare")
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
