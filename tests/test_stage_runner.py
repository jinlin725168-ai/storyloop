"""Tests for one in-memory v2 Stage attempt."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storyloop.models import StoryLoopError
from storyloop.stage_runner import run_stage_attempt
from tests.test_stage_config import loop_config


def judge_evidence(judge: str, phase: str, outcome: str) -> dict:
    status = {"RED": "fail", "EXISTING_GREEN": "pass", "GREEN": "pass",
              "FAIL": "fail", "ERROR": "error", "SKIP": "skip"}[outcome]
    return {"protocol": "loop.judge.v2", "request_id": "a" * 64,
            "stage": "foundation", "judge": judge, "phase": phase,
            "status": status, "summary": outcome, "findings": [],
            "outcome": outcome, "exit_code": {"pass": 0, "fail": 1,
                                                    "error": 2, "skip": 3}[status],
            "contained": True,
            "command_digest": "b" * 64, "before_digest": "c" * 64,
            "after_digest": "c" * 64}


class StageRunnerTests(unittest.TestCase):
    def project(self, folder: str) -> Path:
        project = Path(folder) / "project"
        (project / "service/business").mkdir(parents=True)
        (project / "acceptance").mkdir()
        (project / "openspec/changes/foundation").mkdir(parents=True)
        (project / "acceptance/check.py").write_text("check", encoding="utf-8")
        (project / "openspec/changes/foundation/proposal.md").write_text(
            "spec", encoding="utf-8"
        )
        return project

    def test_red_calls_agent_then_l1_and_l2_in_declaration_order(self):
        config = loop_config()
        calls = []

        def judge(project, value, stage, judge_id, context, **kwargs):
            calls.append(("judge", judge_id))
            outcomes = {"red": "RED", "core": "GREEN", "full": "GREEN"}
            phases = {"red": "red", "core": "l1", "full": "l2"}
            return judge_evidence(judge_id, phases[judge_id], outcomes[judge_id])

        def agent(project, value, role, stage, payload, **kwargs):
            calls.append(("agent", role))
            return {"status": "ok"}

        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.stage_runner.run_stage_judge", side_effect=judge), \
                patch("storyloop.stage_runner.invoke_selected_adapter", side_effect=agent):
            result = run_stage_attempt(self.project(folder), config, "foundation", {"goal": "x"})

        self.assertEqual(calls, [("judge", "red"), ("agent", "implement"),
                                 ("judge", "core"), ("judge", "full")])
        self.assertEqual(result["outcome"], "STAGE_GREEN")
        self.assertTrue(result["agent_invoked"])
        self.assertNotIn("payload", result)

    def test_existing_green_never_calls_agent_but_still_runs_verification(self):
        config = loop_config()
        calls = []

        def judge(project, value, stage, judge_id, context, **kwargs):
            calls.append(judge_id)
            outcome = "EXISTING_GREEN" if judge_id == "red" else "GREEN"
            phase = {"red": "red", "core": "l1", "full": "l2"}[judge_id]
            return judge_evidence(judge_id, phase, outcome)

        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.stage_runner.run_stage_judge", side_effect=judge), \
                patch("storyloop.stage_runner.invoke_selected_adapter") as agent:
            result = run_stage_attempt(self.project(folder), config, "foundation", {})

        self.assertEqual(calls, ["red", "core", "full"])
        agent.assert_not_called()
        self.assertFalse(result["agent_invoked"])
        self.assertEqual(result["changes"], [])

    def test_l1_and_l2_failures_short_circuit_later_judges(self):
        config = loop_config()
        scenarios = (("core", "FAIL", ["red", "core"]),
                     ("full", "ERROR", ["red", "core", "full"]))
        for failing, outcome, expected_calls in scenarios:
            calls = []

            def judge(project, value, stage, judge_id, context, **kwargs):
                calls.append(judge_id)
                current = outcome if judge_id == failing else (
                    "EXISTING_GREEN" if judge_id == "red" else "GREEN"
                )
                phase = {"red": "red", "core": "l1", "full": "l2"}[judge_id]
                return judge_evidence(judge_id, phase, current)

            with self.subTest(failing=failing), tempfile.TemporaryDirectory() as folder, \
                    patch("storyloop.stage_runner.run_stage_judge", side_effect=judge), \
                    patch("storyloop.stage_runner.invoke_selected_adapter") as agent:
                with self.assertRaises(StoryLoopError) as caught:
                    run_stage_attempt(self.project(folder), config, "foundation", {})
                self.assertIn(caught.exception.code, {"JUDGE_FAILED", "JUDGE_ERROR"})
                self.assertEqual(calls, expected_calls)
                agent.assert_not_called()

    def test_agent_ok_cannot_override_a_failing_l1_judge(self):
        config = loop_config()

        def judge(project, value, stage, judge_id, context, **kwargs):
            outcome = {"red": "RED", "core": "FAIL"}.get(judge_id, "GREEN")
            phase = {"red": "red", "core": "l1", "full": "l2"}[judge_id]
            return judge_evidence(judge_id, phase, outcome)

        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.stage_runner.run_stage_judge", side_effect=judge), \
                patch("storyloop.stage_runner.invoke_selected_adapter",
                      return_value={"status": "ok"}):
            with self.assertRaises(StoryLoopError) as caught:
                run_stage_attempt(self.project(folder), config, "foundation", {})

        self.assertEqual(caught.exception.code, "JUDGE_FAILED")

    def test_agent_writes_only_inside_stage_write_paths(self):
        config = loop_config()

        def judge(project, value, stage, judge_id, context, **kwargs):
            outcome = "RED" if judge_id == "red" else "GREEN"
            phase = {"red": "red", "core": "l1", "full": "l2"}[judge_id]
            return judge_evidence(judge_id, phase, outcome)

        def allowed(project, value, role, stage, payload, **kwargs):
            (project / "service/business/feature.py").write_text("ok", encoding="utf-8")
            return {"status": "ok"}

        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.stage_runner.run_stage_judge", side_effect=judge), \
                patch("storyloop.stage_runner.invoke_selected_adapter", side_effect=allowed):
            result = run_stage_attempt(self.project(folder), config, "foundation", {})

        self.assertEqual([(item["path"], item["kind"]) for item in result["changes"]],
                         [("service/business/feature.py", "added")])

    def test_write_path_globs_allow_new_matching_ancestor_directories(self):
        config = loop_config()
        config["stages"][0]["write_paths"] = ["service/*/generated/**"]

        def judge(project, value, stage, judge_id, context, **kwargs):
            outcome = "RED" if judge_id == "red" else "GREEN"
            phase = {"red": "red", "core": "l1", "full": "l2"}[judge_id]
            return judge_evidence(judge_id, phase, outcome)

        def agent(project, value, role, stage, payload, **kwargs):
            path = project / "service/blue/generated/feature.py"
            path.parent.mkdir(parents=True)
            path.write_text("ok", encoding="utf-8")
            return {"status": "ok"}

        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.stage_runner.run_stage_judge", side_effect=judge), \
                patch("storyloop.stage_runner.invoke_selected_adapter", side_effect=agent):
            result = run_stage_attempt(self.project(folder), config, "foundation", {})

        self.assertIn(
            {"path": "service/blue/generated/feature.py", "kind": "added"},
            result["changes"],
        )

    def test_write_path_globs_reject_unmatched_empty_ancestor_directories(self):
        config = loop_config()
        config["stages"][0]["write_paths"] = ["service/*/generated/**"]

        def red(project, value, stage, judge_id, context, **kwargs):
            return judge_evidence("red", "red", "RED")

        def agent(project, value, role, stage, payload, **kwargs):
            (project / "service/arbitrary").mkdir()
            return {"status": "ok"}

        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.stage_runner.run_stage_judge", side_effect=red), \
                patch("storyloop.stage_runner.invoke_selected_adapter", side_effect=agent):
            with self.assertRaisesRegex(StoryLoopError, "escaped write_paths"):
                run_stage_attempt(self.project(folder), config, "foundation", {})

    def test_protected_control_and_outside_writes_are_blocked(self):
        config = loop_config()
        cases = (("acceptance/check.py", "Protected paths changed"),
                 ("README.md", "escaped write_paths"),
                 (".storyloop/state.json", "escaped write_paths"),
                 (".git/config", "escaped write_paths"),
                 ("node_modules/escape.py", "escaped write_paths"))

        def red(project, value, stage, judge_id, context, **kwargs):
            return judge_evidence("red", "red", "RED")

        for relative, expected in cases:
            def write(project, value, role, stage, payload, **kwargs):
                path = project / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("changed", encoding="utf-8")
                return {"status": "ok"}

            with self.subTest(path=relative), tempfile.TemporaryDirectory() as folder, \
                    patch("storyloop.stage_runner.run_stage_judge", side_effect=red), \
                    patch("storyloop.stage_runner.invoke_selected_adapter", side_effect=write):
                with self.assertRaisesRegex(StoryLoopError, expected):
                    run_stage_attempt(self.project(folder), config, "foundation", {})

    def test_uninspectable_post_agent_snapshot_is_workspace_tainted(self):
        config = loop_config()

        def red(project, value, stage, judge_id, context, **kwargs):
            return judge_evidence("red", "red", "RED")

        def write_sensitive(project, value, role, stage, payload, **kwargs):
            (project / ".env").write_text("not-a-secret", encoding="utf-8")
            return {"status": "ok"}

        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.stage_runner.run_stage_judge", side_effect=red), \
                patch("storyloop.stage_runner.invoke_selected_adapter",
                      side_effect=write_sensitive):
            with self.assertRaises(StoryLoopError) as caught:
                run_stage_attempt(self.project(folder), config, "foundation", {})

        self.assertEqual(caught.exception.code, "WORKSPACE_TAINTED")
    def test_contained_agent_failure_still_checks_write_boundaries(self):
        config = loop_config()

        def red(project, value, stage, judge_id, context, **kwargs):
            return judge_evidence("red", "red", "RED")

        def failing_agent(project, value, role, stage, payload, **kwargs):
            (project / "README.md").write_text("escaped", encoding="utf-8")
            raise StoryLoopError("Agent process failed")

        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.stage_runner.run_stage_judge", side_effect=red), \
                patch("storyloop.stage_runner.invoke_selected_adapter",
                      side_effect=failing_agent):
            with self.assertRaisesRegex(StoryLoopError, "escaped write_paths"):
                run_stage_attempt(self.project(folder), config, "foundation", {})

    def test_contained_agent_os_error_still_checks_write_boundaries(self):
        config = loop_config()

        def red(project, value, stage, judge_id, context, **kwargs):
            return judge_evidence("red", "red", "RED")

        def interrupted_agent(project, value, role, stage, payload, **kwargs):
            (project / "README.md").write_text("escaped", encoding="utf-8")
            raise OSError("adapter interrupted")

        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.stage_runner.run_stage_judge", side_effect=red), \
                patch("storyloop.stage_runner.invoke_selected_adapter",
                      side_effect=interrupted_agent):
            with self.assertRaisesRegex(StoryLoopError, "escaped write_paths"):
                run_stage_attempt(self.project(folder), config, "foundation", {})

    def test_nonterminal_agent_status_propagates_without_running_later_judges(self):
        config = loop_config()
        for code in ("NEEDS_INPUT", "ARCHITECTURE_DECISION_REQUIRED"):
            calls = []

            def red(project, value, stage, judge_id, context, **kwargs):
                calls.append(judge_id)
                return judge_evidence("red", "red", "RED")

            with self.subTest(code=code), tempfile.TemporaryDirectory() as folder, \
                    patch("storyloop.stage_runner.run_stage_judge", side_effect=red), \
                    patch("storyloop.stage_runner.invoke_selected_adapter",
                          side_effect=StoryLoopError("pause", code=code)):
                with self.assertRaises(StoryLoopError) as caught:
                    run_stage_attempt(self.project(folder), config, "foundation", {})
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(calls, ["red"])

    def test_invalid_payload_and_unknown_stage_fail_before_any_execution(self):
        config = loop_config()
        for stage, payload, expected in (("missing", {}, "Unknown stage"),
                                         ("foundation", [], "JSON object"),
                                         ("foundation", {"bad": object()}, "bounded JSON")):
            with self.subTest(expected=expected), \
                    patch("storyloop.stage_runner.run_stage_judge") as judge, \
                    patch("storyloop.stage_runner.invoke_selected_adapter") as agent:
                with self.assertRaisesRegex(StoryLoopError, expected):
                    run_stage_attempt(Path.cwd(), config, stage, payload)
                judge.assert_not_called()
                agent.assert_not_called()

    def test_shared_attempt_deadline_stops_later_operations(self):
        config = loop_config()
        now = [0]
        calls = []

        def judge(project, value, stage, judge_id, context, **kwargs):
            calls.append(("judge", judge_id, kwargs["attempt_deadline_ms"]))
            if judge_id == "core":
                now[0] = 1_000
            outcome = "RED" if judge_id == "red" else "GREEN"
            phase = {"red": "red", "core": "l1", "full": "l2"}[judge_id]
            return judge_evidence(judge_id, phase, outcome)

        def agent(project, value, role, stage, payload, **kwargs):
            calls.append(("agent", role, kwargs["attempt_deadline_ms"]))
            now[0] = 500
            return {"status": "ok"}

        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.stage_runner.run_stage_judge", side_effect=judge), \
                patch("storyloop.stage_runner.invoke_selected_adapter", side_effect=agent):
            with self.assertRaises(StoryLoopError) as caught:
                run_stage_attempt(
                    self.project(folder), config, "foundation", {},
                    attempt_deadline_ms=1_000, budget_clock_ms=lambda: now[0],
                )

        self.assertEqual(caught.exception.code, "TIME_BUDGET")
        self.assertEqual(calls, [("judge", "red", 1_000),
                                 ("agent", "implement", 1_000),
                                 ("judge", "core", 1_000)])
    def test_real_red_agent_l1_l2_chain(self):
        config = loop_config()
        agent_code = (
            "import json,sys; from pathlib import Path; r=json.load(sys.stdin); "
            "p=Path('service/business/feature.py'); p.write_text('ready',encoding='utf-8'); "
            "print(json.dumps({'protocol':'loop.agent.v2','request_id':r['request_id'],"
            "'status':'ok','summary':'implemented','questions':[]}))"
        )
        judge_code = (
            "import json,sys; from pathlib import Path; r=json.load(sys.stdin); "
            "ready=Path('service/business/feature.py').exists(); "
            "red=r['phase']=='red' and not ready; status='fail' if red else 'pass'; "
            "print(json.dumps({'protocol':'loop.judge.v2','request_id':r['request_id'],"
            "'stage':r['stage'],'judge':r['judge'],'phase':r['phase'],'status':status,"
            "'summary':'checked','findings':['missing'] if red else []})); "
            "sys.exit(1 if red else 0)"
        )
        config["agent"]["adapters"]["claude"]["command"] = [sys.executable, "-c", agent_code]
        for judge in config["stages"][0]["judges"]:
            judge["command"] = [sys.executable, "-c", judge_code]

        with tempfile.TemporaryDirectory() as folder:
            project = self.project(folder)
            result = run_stage_attempt(
                project, config, "foundation", {"goal": "implement"},
                requested_adapter="claude", resolver=lambda name: sys.executable,
            )

        self.assertEqual(result["outcome"], "STAGE_GREEN")
        self.assertTrue(result["agent_invoked"])
        self.assertEqual([item["judge"] for item in result["l1"]], ["core"])
        self.assertEqual([item["judge"] for item in result["l2"]], ["full"])


if __name__ == "__main__":
    unittest.main()
