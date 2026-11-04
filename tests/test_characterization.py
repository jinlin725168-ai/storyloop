"""Before implementation, existing behaviour on the paths about to change is locked by tests that are green first."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from tests.routing_support import legacy_create_change as create_change
from storyloop.project_loop import LoopServices, load_run_state, run_change
from storyloop.project_profile import matches_test_root
from tests.support import product
from tests.test_baseline_behavior import git, repository
from tests.test_project_loop import smoke_payload

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)
UNIT = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."]
GREEN_TEST = ("import unittest\nfrom modules.orders import service\n\n\nclass Keep(unittest.TestCase):\n"
              "    def test_value_stays_present(self):\n        self.assertIn('VALUE', dir(service))\n")


def _repo(settings: dict):
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    repository(root)
    (root / "modules/orders").mkdir(parents=True)
    (root / "modules/__init__.py").write_text("", encoding="utf-8")
    (root / "modules/orders/__init__.py").write_text("", encoding="utf-8")
    (root / "modules/orders/service.py").write_text("VALUE = 0\n", encoding="utf-8")
    (root / "modules/orders/other.py").write_text("OTHER = 0\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests/__init__.py").write_text("", encoding="utf-8")
    # Python 3.12's unittest exits 5 when it discovers no tests; a real project always has one.
    (root / "tests/test_existing.py").write_text(
        "import unittest\n\n\nclass Existing(unittest.TestCase):\n    def test_truth(self):\n"
        "        self.assertTrue(True)\n", encoding="utf-8")
    git(root, "add", "."); git(root, "commit", "-q", "-m", "existing behaviour")
    bootstrap_project(root, settings)
    state = create_change(root, "orders", product(), now=lambda: NOW)
    approve_change(root, state["change_id"], now=lambda: NOW)
    return temporary, root, state["change_id"]


def _review(project, context):
    return {"verdict": "pass", "summary": "ok", "findings": [], "questions": []}


class TestRootMatchingTests(unittest.TestCase):
    def test_patterns_cover_directories_globs_and_double_stars(self):
        roots = ["tests", "backend/**/src/test/**", "frontend/src/**/*.test.ts"]
        self.assertTrue(matches_test_root("tests/test_x.py", roots))
        self.assertTrue(matches_test_root("backend/ruoyi-modules/ruoyi-biz/src/test/java/A.java", roots))
        self.assertTrue(matches_test_root("frontend/src/utils/date.test.ts", roots))
        self.assertFalse(matches_test_root("backend/ruoyi-modules/ruoyi-biz/src/main/java/A.java", roots))
        self.assertFalse(matches_test_root("frontend/src/utils/date.ts", roots))
        self.assertFalse(matches_test_root("tests2/x.py", roots))


class CharacterizationGateTests(unittest.TestCase):
    def test_claim_may_lock_unchanged_behavior_of_touched_symbol(self):
        from storyloop.characterization_locks import validate_behavior_claims
        answer = {"tests": ["tests/test_characterize_service.py"], "claims": [{
            "test": "tests/test_characterize_service.py", "behavior": "old service wording",
            "symbols": [{"path": "modules/orders/service.py", "symbol": "serve"}],
            "criterion_ids": []}]}
        validate_behavior_claims(answer, set())

    def test_unclaimed_new_test_is_not_silently_added_to_locks(self):
        temporary, root, change_id = _repo({"unit_commands": [UNIT], "test_roots": ["tests"]})
        calls = []

        def characterize(project, context):
            calls.append(context)
            if len(calls) == 1:
                (project / "tests/test_characterize_unclaimed.py").write_text(GREEN_TEST,
                                                                                encoding="utf-8")
            else:
                self.assertFalse((project / "tests/test_characterize_unclaimed.py").exists())
            return self._characterize(project, context)

        try:
            services = self._services(change_id, lambda p, c: {
                "summary": "done", "questions": []}, characterize, [])
            run_change(root, change_id, services)
            self.assertEqual(len(calls), 2)
        finally:
            temporary.cleanup()

    def test_existing_project_test_is_rejected_then_role_creates_own_file(self):
        temporary, root, change_id = _repo({"unit_commands": [UNIT], "test_roots": ["tests"]})
        original = (root / "tests/test_existing.py").read_bytes()
        calls = []

        def characterize(project, context):
            calls.append(context)
            if len(calls) == 1:
                (project / "tests/test_existing.py").write_text("# overwritten\n", encoding="utf-8")
                return {"summary": "wrong file", "touched": ["modules/orders/service.py"],
                        "tests": ["tests/test_existing.py"], "questions": [], "claims": []}
            self.assertIn("validation_errors", context)
            self.assertEqual((project / "tests/test_existing.py").read_bytes(), original)
            return self._characterize(project, context)

        def implement(project, context):
            (project / "modules/orders/service.py").write_text("VALUE = 1\n", encoding="utf-8")
            (project / "tests/test_existing.py").write_text(
                "import unittest\nclass Existing(unittest.TestCase):\n"
                "    def test_new_value(self): self.assertEqual(1, 1)\n", encoding="utf-8")
            return {"summary": "done", "questions": []}

        try:
            services = self._services(change_id, implement, characterize, [])
            self.assertEqual(run_change(root, change_id, services)["status"], "PASSED")
            self.assertEqual(len(calls), 2)
            state = load_run_state(root, change_id)
            self.assertNotIn("tests/test_existing.py", state["characterization"]["tests"])
            self.assertEqual(state["characterization"]["claims"]["tests/test_characterize_service.py"]
                             ["behavior"], "VALUE remains present")
            self.assertNotIn("tests/test_existing.py", {
                test for artifacts in state["characterization"].get("locks", {}).values()
                for test in artifacts})
        finally:
            temporary.cleanup()

    def test_claim_overlapping_changed_criterion_is_rejected_and_rewritten(self):
        temporary, root, change_id = _repo({"unit_commands": [UNIT], "test_roots": ["tests"]})
        calls = []

        def characterize(project, context):
            calls.append(context)
            answer = self._characterize(project, context)
            if len(calls) == 1:
                answer["claims"][0]["criterion_ids"] = ["AC-1"]
            return answer

        try:
            services = self._services(change_id, lambda p, c: {
                "summary": "done", "questions": []}, characterize, [])
            run_change(root, change_id, services)
            self.assertEqual(len(calls), 2)
            self.assertIn("AC-1", str(calls[1]["validation_errors"]))
        finally:
            temporary.cleanup()

    def test_reviewed_characterization_conflict_restarts_without_spending_attempt(self):
        temporary, root, change_id = _repo({"unit_commands": [UNIT], "test_roots": ["tests"]})
        characterized = []
        implemented = []
        reviewed = []

        def characterize(project, context):
            characterized.append(context)
            return self._characterize(project, context)

        def implement(project, context):
            implemented.append(context["attempt"])
            (project / "modules/orders/service.py").write_text("VALUE = 1\n", encoding="utf-8")
            return {"summary": "done", "questions": []}

        def review(project, context):
            reviewed.append(context)
            if len(reviewed) == 1:
                return {"verdict": "fail", "summary": "old behavior lock conflicts with AC-1",
                        "findings": [{"target": "oracle", "criterion_id": "AC-1",
                                      "location": "tests/test_characterize_service.py:5",
                                      "message": "characterization locks changed behavior"}],
                        "questions": []}
            return _review(project, context)

        try:
            services = self._services(change_id, implement, characterize, [])
            services = LoopServices(generate_smoke=services.generate_smoke,
                                    implement=services.implement, review=review,
                                    characterize=services.characterize)
            self.assertEqual(run_change(root, change_id, services)["status"], "PASSED")
            self.assertEqual(len(characterized), 2)
            self.assertEqual(implemented, [1, 1])
            self.assertEqual(load_run_state(root, change_id)["attempt"], 1)
        finally:
            temporary.cleanup()

    def test_repeated_conflict_stops_after_one_restart_and_explains_the_rejected_lock(self):
        temporary, root, change_id = _repo({"unit_commands": [UNIT], "test_roots": ["tests"]})
        contexts = []

        def characterize(project, context):
            contexts.append(context)
            self.assertLessEqual(len(contexts), 2, 'characterization restarted more than once')
            return self._characterize(project, context)

        def implement(project, context):
            (project / "modules/orders/service.py").write_text("VALUE = 1\n", encoding="utf-8")
            return {"summary": "done", "questions": []}

        def review(project, context):
            return {"verdict": "fail", "summary": "old wording conflicts with AC-1",
                    "findings": [{"target": "oracle", "criterion_id": "AC-1",
                                  "location": "tests/test_characterize_service.py:5",
                                  "message": "old wording must change"}], "questions": []}

        try:
            services = LoopServices(generate_smoke=lambda p, c: smoke_payload(change_id),
                                    implement=implement, review=review, characterize=characterize)
            self.assertEqual(run_change(root, change_id, services)["status"], "ORACLE_DEFECT")
            self.assertEqual(len(contexts), 2)
            self.assertIn("tests/test_characterize_service.py",
                          str(contexts[1]["conflict_review"]))
            self.assertIn("old wording must change", str(contexts[1]["conflict_review"]))
            self.assertEqual(load_run_state(root, change_id)["attempt"], 1)
        finally:
            temporary.cleanup()

    def test_saved_reviewed_characterization_conflict_resumes_the_same_restart(self):
        from unittest.mock import patch
        temporary, root, change_id = _repo({"unit_commands": [UNIT], "test_roots": ["tests"]})
        calls = []

        def characterize(project, context):
            calls.append(context)
            return self._characterize(project, context)

        def implement(project, context):
            (project / "modules/orders/service.py").write_text("VALUE = 1\n", encoding="utf-8")
            return {"summary": "done", "questions": []}

        def review(project, context):
            if len(calls) == 1:
                return {"verdict": "fail", "summary": "old lock",
                        "findings": [{"target": "oracle", "criterion_id": "AC-1",
                                      "location": "tests/test_characterize_service.py:5",
                                      "message": "locks changed behavior"}], "questions": []}
            return _review(project, context)

        try:
            services = LoopServices(generate_smoke=lambda p, c: smoke_payload(change_id),
                                    implement=implement, review=review, characterize=characterize)
            with patch("storyloop.project_loop_failures.discard_characterization_attempt",
                       return_value=False):
                self.assertEqual(run_change(root, change_id, services)["status"], "ORACLE_DEFECT")
            self.assertEqual(run_change(root, change_id, services)["status"], "PASSED")
            self.assertEqual(len(calls), 2)
            final = load_run_state(root, change_id)
            self.assertEqual(final["attempt"], 1)
            self.assertFalse(any(item.get("attribution") == "oracle" for item in final["failures"]))
        finally:
            temporary.cleanup()

    def _paused_conflict(self):
        from storyloop.changes import load_change
        from storyloop.project_loop_failures import route_review
        temporary, root, change_id = _repo({"unit_commands": [UNIT], "test_roots": ["tests"]})
        services = self._services(change_id, lambda p, c: {
            "summary": "pause before implementation", "questions": ["wait"]},
            self._characterize, [])
        self.assertEqual(run_change(root, change_id, services)["status"], "NEEDS_INPUT")
        state = load_run_state(root, change_id)
        review = {"verdict": "fail", "summary": "lock conflicts",
                  "findings": [{"target": "oracle", "criterion_id": "AC-1",
                                "location": "tests/test_characterize_service.py:5",
                                "message": "old wording conflicts"}], "questions": []}
        self.assertEqual(route_review(root, load_change(root, change_id), state, review)["status"],
                         "ORACLE_DEFECT")
        return temporary, root, change_id, services

    def test_extra_commit_after_lock_keeps_oracle_stop_instead_of_hard_error(self):
        from storyloop.models import StoryLoopError
        from tests.test_baseline_behavior import git
        temporary, root, change_id, services = self._paused_conflict()
        try:
            git(root, "commit", "--allow-empty", "-qm", "independent follow-up")
            head = git(root, "rev-parse", "HEAD").strip()
            with self.assertRaisesRegex(StoryLoopError, "ORACLE_DEFECT.*supersede"):
                run_change(root, change_id, services)
            self.assertEqual(git(root, "rev-parse", "HEAD").strip(), head)
            self.assertEqual(load_run_state(root, change_id)["status"], "ORACLE_DEFECT")
        finally:
            temporary.cleanup()

    def test_hand_edits_after_oracle_stop_are_not_discarded_on_resume(self):
        from storyloop.models import StoryLoopError
        temporary, root, change_id, services = self._paused_conflict()
        changed = root / "modules/orders/service.py"
        changed.write_text("VALUE = 99\n", encoding="utf-8")
        try:
            with self.assertRaisesRegex(StoryLoopError, "changed since ORACLE_DEFECT"):
                run_change(root, change_id, services)
            self.assertEqual(changed.read_text(encoding="utf-8"), "VALUE = 99\n")
            self.assertEqual(load_run_state(root, change_id)["status"], "ORACLE_DEFECT")
        finally:
            temporary.cleanup()

    def test_discarded_lock_keeps_the_old_unit_count_floor(self):
        from storyloop.project_boundaries import audit_project_changes
        from storyloop.project_loop_failures import discard_characterization_attempt
        temporary, root, change_id, _ = self._paused_conflict()
        try:
            state = load_run_state(root, change_id)
            before = state['oracle']['baseline']['unit']
            boundary = audit_project_changes(root, change_id)
            self.assertTrue(discard_characterization_attempt(root, state, boundary, []))
            self.assertEqual(load_run_state(root, change_id)['oracle']['baseline']['unit'], before)
        finally:
            temporary.cleanup()

    def _services(self, change_id, implement, characterize, calls):
        def wrapped(project, context):
            calls.append(context)
            return characterize(project, context)
        return LoopServices(generate_smoke=lambda project, state: smoke_payload(change_id),
                            implement=implement, review=_review, characterize=wrapped)

    def _characterize(self, project, context):
        (project / "tests/test_characterize_service.py").write_text(GREEN_TEST, encoding="utf-8")
        return {"summary": "locked service", "touched": ["modules/orders/service.py"],
                "tests": ["tests/test_characterize_service.py"], "questions": [],
                "claims": [{"test": "tests/test_characterize_service.py",
                            "behavior": "VALUE remains present", "symbols": [], "criterion_ids": []}]}

    def test_characterization_runs_green_before_the_first_attempt_and_is_committed(self):
        temporary, root, change_id = _repo({"unit_commands": [UNIT], "test_roots": ["tests"]})
        calls: list = []
        order: list = []

        def implement(project, context):
            order.append(("implement", context["characterization"]["touched"]))
            (project / "modules/orders/service.py").write_text("VALUE = 1\n", encoding="utf-8")
            return {"summary": "done", "questions": []}

        try:
            result = run_change(root, change_id, self._services(change_id, implement, self._characterize, calls))
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(len(calls), 1)
            self.assertEqual(order, [("implement", ["modules/orders/service.py"])])
            state = load_run_state(root, change_id)
            self.assertEqual(state["characterization"]["status"], "GREEN")
            subjects = git(root, "log", "--format=%s", "-6").splitlines()
            self.assertIn(f"test({change_id}): characterize touched paths", subjects)
            self.assertTrue((root / "tests/test_characterize_service.py").is_file())
        finally:
            temporary.cleanup()

    def test_modifying_an_undeclared_existing_file_is_reverted_characterized_and_retried(self):
        temporary, root, change_id = _repo({"unit_commands": [UNIT], "test_roots": ["tests"]})
        calls: list = []
        attempts: list = []

        def characterize(project, context):
            if context.get("extend"):
                (project / "tests/test_characterize_other.py").write_text(
                    GREEN_TEST.replace("service", "other").replace("VALUE", "OTHER"), encoding="utf-8")
                return {"summary": "locked other", "touched": context["extend"],
                        "tests": ["tests/test_characterize_other.py"], "questions": [],
                        "claims": [{"test": "tests/test_characterize_other.py",
                                    "behavior": "OTHER remains present", "symbols": [], "criterion_ids": []}]}
            return self._characterize(project, context)

        def implement(project, context):
            attempts.append(context["attempt"])
            (project / "modules/orders/service.py").write_text("VALUE = 1\n", encoding="utf-8")
            if context["attempt"] == 1:
                (project / "modules/orders/other.py").write_text("OTHER = 1\n", encoding="utf-8")
            return {"summary": "done", "questions": []}

        try:
            result = run_change(root, change_id, self._services(change_id, implement, characterize, calls))
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(attempts, [1, 2])
            self.assertEqual(calls[1]["extend"], ["modules/orders/other.py"])
            state = load_run_state(root, change_id)
            self.assertEqual(state["failures"][0]["characterization"]["uncharacterized"], ["modules/orders/other.py"])
            self.assertEqual(sorted(state["characterization"]["touched"]),
                             ["modules/orders/other.py", "modules/orders/service.py"])
            self.assertEqual((root / "modules/orders/other.py").read_text(encoding="utf-8"), "OTHER = 0\n")
        finally:
            temporary.cleanup()

    def test_red_characterization_or_non_test_writes_stop_the_run(self):
        temporary, root, change_id = _repo({"unit_commands": [UNIT], "test_roots": ["tests"]})
        try:
            def red(project, context):
                (project / "tests/test_characterize_service.py").write_text(
                    GREEN_TEST.replace("assertIn", "assertNotIn"), encoding="utf-8")
                return {"summary": "wrong lock", "touched": ["modules/orders/service.py"],
                        "tests": ["tests/test_characterize_service.py"], "questions": [],
                        "claims": [{"test": "tests/test_characterize_service.py",
                                    "behavior": "VALUE stays absent", "symbols": [], "criterion_ids": []}]}
            result = run_change(root, change_id, self._services(change_id, lambda p, c: None, red, []))
            self.assertEqual(result["status"], "CHARACTERIZATION_FAILED")
        finally:
            temporary.cleanup()
        temporary, root, change_id = _repo({"unit_commands": [UNIT], "test_roots": ["tests"]})
        try:
            def leaks(project, context):
                (project / "modules/orders/service.py").write_text("VALUE = 2\n", encoding="utf-8")
                return {"summary": "edited code", "touched": [], "tests": [],
                        "questions": [], "claims": []}
            result = run_change(root, change_id, self._services(change_id, lambda p, c: None, leaks, []))
            self.assertEqual(result["status"], "BOUNDARY_VIOLATION")
        finally:
            temporary.cleanup()

    def test_a_red_characterization_resumes_after_the_human_fixes_the_tests(self):
        temporary, root, change_id = _repo({"unit_commands": [UNIT], "test_roots": ["tests"]})
        calls: list = []

        def red(project, context):
            (project / "tests/test_characterize_service.py").write_text(
                GREEN_TEST.replace("assertIn", "assertNotIn"), encoding="utf-8")
            return {"summary": "wrong lock", "touched": ["modules/orders/service.py"],
                    "tests": ["tests/test_characterize_service.py"], "questions": [],
                    "claims": [{"test": "tests/test_characterize_service.py",
                                "behavior": "VALUE stays absent", "symbols": [], "criterion_ids": []}]}

        def implement(project, context):
            (project / "modules/orders/service.py").write_text("VALUE = 1\n", encoding="utf-8")
            return {"summary": "done", "questions": []}

        services = self._services(change_id, implement, red, calls)
        try:
            self.assertEqual(run_change(root, change_id, services)["status"], "CHARACTERIZATION_FAILED")
            # Still red: the run pauses again at the same gate instead of refusing to resume.
            self.assertEqual(run_change(root, change_id, services)["status"], "CHARACTERIZATION_FAILED")
            (root / "tests/test_characterize_service.py").write_text(GREEN_TEST, encoding="utf-8")
            result = run_change(root, change_id, services)
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(len(calls), 1)  # the human's fix is re-verified, the agent is not asked again
            state = load_run_state(root, change_id)
            self.assertEqual(state["characterization"]["status"], "GREEN")
            self.assertEqual(state["characterization"]["touched"], ["modules/orders/service.py"])
            self.assertNotIn("pending_characterization", state)
            subjects = git(root, "log", "--format=%s", "-8").splitlines()
            self.assertIn(f"test({change_id}): characterize touched paths", subjects)
            committed = git(root, "log", "--format=%H", "--grep", "characterize touched paths", "-1").strip()
            self.assertIn("assertIn(", git(root, "show", f"{committed}:tests/test_characterize_service.py"))
        finally:
            temporary.cleanup()

    def test_gate_is_skipped_when_the_project_declares_no_test_roots(self):
        temporary, root, change_id = _repo({"unit_commands": [UNIT]})
        calls: list = []

        def implement(project, context):
            (project / "modules/orders/service.py").write_text("VALUE = 1\n", encoding="utf-8")
            return {"summary": "done", "questions": []}

        try:
            result = run_change(root, change_id, self._services(change_id, implement, self._characterize, calls))
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(calls, [])
            self.assertEqual(load_run_state(root, change_id)["characterization"]["status"], "SKIPPED")
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
