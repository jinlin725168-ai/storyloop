"""The Claude Code adapter resumes the same session when a segment hits its turn cap."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import unittest
import tempfile
import sys

from storyloop.baseline import bootstrap_project
from storyloop.project_agents import invoke_project_agent
from storyloop.storage import load_json, write_json
from storyloop.telemetry import ledger_entries
from tests.test_baseline_behavior import repository
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "claude_agent", Path(__file__).resolve().parents[1] / "examples/claude-code/claude_agent.py")
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


def envelope(subtype: str, result: str, session: str, cost: float, turns: int) -> str:
    return json.dumps({"type": "result", "subtype": subtype, "is_error": subtype != "success",
                       "result": result, "session_id": session, "total_cost_usd": cost,
                       "num_turns": turns, "duration_ms": 1000, "usage": {"output_tokens": 5},
                       "modelUsage": {"claude-x": {}}})


class Completed:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout, self.stderr, self.returncode = stdout, "", returncode


class ClaudeAdapterTests(unittest.TestCase):
    def test_retrospective_uses_one_cli_session_and_its_schema(self):
        from storyloop.role_schemas import schema_for, wire_schema
        from storyloop.role_subagents import subagents_for
        reply = {'lessons': [], 'errata': [], 'uncovered_concepts': [], 'nothing_to_learn': 'none'}
        schema = wire_schema(schema_for('review', {'mode': 'retrospective'}))
        request = {'role': 'review', 'project': '/tmp/p', 'payload': {'mode': 'retrospective'},
                   'response_schema': schema, 'instructions': 'Read only retrospective'}
        calls = []
        def fake_run(command, **kwargs):
            calls.append(command)
            return Completed(envelope('success', json.dumps(reply), 'retro-session', 0.1, 1))
        with patch.object(adapter.subprocess, 'run', fake_run), patch.object(
                adapter.shutil, 'which', return_value='/usr/bin/claude'), patch(
                'sys.stdin', io.StringIO(json.dumps(request))), patch(
                'sys.stdout', new_callable=io.StringIO) as out, patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(adapter.main_with_args([]), 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(calls[0][calls[0].index('--json-schema') + 1]), schema)
        passed_schema = json.loads(calls[0][calls[0].index('--json-schema') + 1])
        action = passed_schema['properties']['lessons']['items']['properties']['action']
        self.assertEqual(action['enum'], ['create', 'update', 'retire'])
        result = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(result['nothing_to_learn'], 'none')
        self.assertEqual(result['_telemetry']['subagents_applied']['perspectives'], 0)
        self.assertEqual(subagents_for('review', 'heavy', request['payload'], {} )['perspectives'], [])

    def test_turn_cap_resumes_the_session_and_sums_telemetry(self):
        calls = []
        replies = [envelope("error_max_turns", "", "sid-1", 0.5, 60),
                   envelope("error_max_turns", "", "sid-1", 0.4, 60),
                   envelope("success", '{"summary":"done","questions":[]}', "sid-1", 0.1, 7)]

        def fake_run(command, **kwargs):
            calls.append((list(command), kwargs.get("input")))
            reply = replies[len(calls) - 1]
            return Completed(reply, returncode=0 if '"success"' in reply else 1)  # claude exits 1 on max turns

        request = {"role": "implement", "project": "/tmp/p", "payload": {}, "instructions": "do it"}
        with patch.object(adapter.subprocess, "run", fake_run), \
                patch.object(adapter.shutil, "which", return_value="/usr/bin/claude"), \
                patch("sys.stdin", io.StringIO(json.dumps(request))), \
                patch("sys.stdout", new_callable=io.StringIO) as out, \
                patch("sys.stderr", new_callable=io.StringIO) as err:
            code = adapter.main_with_args(["--segment-turns", "60", "--max-segments", "5"])
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 3)
        self.assertIn("segment 1 hit the turn cap", err.getvalue())
        self.assertIn("cost=$1.0000", err.getvalue())
        self.assertNotIn("--resume", calls[0][0])
        self.assertIn("--resume", calls[1][0])
        self.assertEqual(calls[1][0][calls[1][0].index("--resume") + 1], "sid-1")
        self.assertIn("继续", calls[1][1])
        result = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(result["summary"], "done")
        self.assertAlmostEqual(result["_telemetry"]["cost_usd"], 1.0)
        self.assertEqual(result["_telemetry"]["turns"], 127)
        self.assertEqual(result["_telemetry"]["segments"], 3)

    def test_segment_budget_exhausted_is_a_failure(self):
        replies = [envelope("error_max_turns", "", "sid-2", 0.5, 60)] * 3
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            return Completed(replies[len(calls) - 1], returncode=1)

        request = {"role": "review", "project": "/tmp/p", "payload": {}, "instructions": "check"}
        with patch.object(adapter.subprocess, "run", fake_run), \
                patch.object(adapter.shutil, "which", return_value="/usr/bin/claude"), \
                patch("sys.stdin", io.StringIO(json.dumps(request))), \
                patch("sys.stdout", new_callable=io.StringIO), \
                patch("sys.stderr", new_callable=io.StringIO) as err:
            code = adapter.main_with_args(["--segment-turns", "60", "--max-segments", "3",
                                           "--review-perspectives", "none"])
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 3)
        self.assertIn("segments", err.getvalue())

    def test_a_prose_ending_is_repaired_by_asking_the_session_for_its_json(self):
        replies = [envelope("success", "Done. The suite is written under acceptance/changes/…", "sid-3", 0.4, 30),
                   envelope("success", json.dumps({"files": [], "manifest": {"version": 1, "tests": []}}), "sid-3", 0.1, 1)]
        calls = []
        inputs = []

        def fake_run(command, **kwargs):
            calls.append(command)
            inputs.append(kwargs.get("input"))
            return Completed(replies[len(calls) - 1], returncode=0)

        request = {"role": "acceptance", "project": "/tmp/p", "payload": {}, "instructions": "write"}
        with patch.object(adapter.subprocess, "run", fake_run), \
                patch.object(adapter.shutil, "which", return_value="/usr/bin/claude"), \
                patch("sys.stdin", io.StringIO(json.dumps(request))), \
                patch("sys.stdout", new_callable=io.StringIO) as out, \
                patch("sys.stderr", new_callable=io.StringIO) as err:
            code = adapter.main_with_args([])
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        self.assertIn("--resume", calls[1])
        self.assertEqual(inputs[1], adapter.REPAIR_PROMPT)
        self.assertIn("repair 1/2", err.getvalue())
        self.assertEqual(json.loads(out.getvalue())["manifest"]["version"], 1)


class ReviewPerspectiveTests(unittest.TestCase):
    """review fans out to perspective sub-agents (parallel claude sessions) and a lead merges the verdict."""

    def _request(self):
        return {"role": "review", "project": "/tmp/p", "instructions": "review it",
                "payload": {"change": {"change_id": "FEAT-1"}, "layers": {}, "design_plan": None}}

    def test_perspectives_run_and_the_lead_merges_them_into_one_verdict(self):
        calls = []

        def fake_run(command, **kwargs):
            prompt = kwargs.get("input", "")
            calls.append(prompt)
            if adapter.LEAD_MARK in prompt:
                self.assertIn("security", prompt)  # the lead sees every perspective's output
                self.assertIn("SQL built by string concatenation", prompt)
                result = {"verdict": "fail", "summary": "security found a real defect",
                          "lessons_used": [{"id": "L-0001", "how": "checked"}], "errata": [],
                          "findings": [{"perspective": "security", "message": "SQL built by string concatenation"}],
                          "questions": []}
                return Completed(envelope("success", json.dumps(result), "lead", 0.2, 3))
            for name in adapter.REVIEW_PERSPECTIVES:
                if adapter.PERSPECTIVE_MARK.format(name=name) in prompt:
                    findings = ([{"message": "SQL built by string concatenation", "evidence": "Mapper.xml:12"}]
                                if name == "security" else [])
                    result = {"summary": f"{name} looked", "findings": findings, "questions": []}
                    return Completed(envelope("success", json.dumps(result), f"sid-{name}", 0.1, 2))
            raise AssertionError("unexpected prompt")

        with patch.object(adapter.subprocess, "run", fake_run), \
                patch.object(adapter.shutil, "which", return_value="/usr/bin/claude"), \
                patch("sys.stdin", io.StringIO(json.dumps(self._request()))), \
                patch("sys.stdout", new_callable=io.StringIO) as out, \
                patch("sys.stderr", new_callable=io.StringIO):
            code = adapter.main_with_args([])
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 4 + 1)
        result = json.loads(out.getvalue())
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["findings"][0]["perspective"], "security")
        self.assertEqual(result["lessons_used"], [{"id": "L-0001", "how": "checked"}])
        self.assertEqual(result["errata"], [])
        telemetry = result["_telemetry"]
        self.assertEqual(telemetry["perspectives"], 4)
        self.assertAlmostEqual(telemetry["cost_usd"], 0.1 * 4 + 0.2)
        self.assertEqual(telemetry["turns"], 2 * 4 + 3)

    def test_perspectives_can_be_disabled_for_a_single_review_session(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(kwargs.get("input", ""))
            result = {"verdict": "pass", "summary": "ok", "findings": [], "questions": []}
            return Completed(envelope("success", json.dumps(result), "one", 0.3, 5))

        with patch.object(adapter.subprocess, "run", fake_run), \
                patch.object(adapter.shutil, "which", return_value="/usr/bin/claude"), \
                patch("sys.stdin", io.StringIO(json.dumps(self._request()))), \
                patch("sys.stdout", new_callable=io.StringIO) as out, \
                patch("sys.stderr", new_callable=io.StringIO):
            code = adapter.main_with_args(["--review-perspectives", "none"])
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        self.assertNotIn(adapter.LEAD_MARK, calls[0])
        self.assertEqual(json.loads(out.getvalue())["verdict"], "pass")

    def test_a_failing_perspective_fails_the_whole_review_call(self):
        def fake_run(command, **kwargs):
            prompt = kwargs.get("input", "")
            if adapter.PERSPECTIVE_MARK.format(name="tests") in prompt:
                return Completed(envelope("error_during_execution", "boom", "sid-tests", 0.1, 1), returncode=1)
            result = {"summary": "fine", "findings": [], "questions": []}
            return Completed(envelope("success", json.dumps(result), "sid", 0.1, 2))

        with patch.object(adapter.subprocess, "run", fake_run), \
                patch.object(adapter.shutil, "which", return_value="/usr/bin/claude"), \
                patch("sys.stdin", io.StringIO(json.dumps(self._request()))), \
                patch("sys.stdout", new_callable=io.StringIO), \
                patch("sys.stderr", new_callable=io.StringIO) as err:
            code = adapter.main_with_args([])
        self.assertEqual(code, 1)
        self.assertIn("tests", err.getvalue())


class ImplementLaneTests(unittest.TestCase):
    """implement splits a full-stack change into per-layer lanes (parallel sessions) and integrates."""

    LANES = ["--implement-lanes", "backend=backend/,frontend=frontend/src/,sql=sql/"]

    def _request(self, plan=True):
        return {"role": "implement", "project": "/tmp/p", "instructions": "implement it",
                "payload": {"change": {"change_id": "FEAT-1"}, "attempt": 1, "failures": [], "answers": [],
                            "characterization": None,
                            "design_plan": ({"summary": "s", "touched": ["backend/A.java"],
                                             "design": "## Files\n- backend/A.java\n- frontend/src/x.vue\n"}
                                            if plan else None)}}

    def test_lead_plans_lanes_run_in_parallel_and_the_lead_integrates(self):
        calls = []

        def fake_run(command, **kwargs):
            prompt = kwargs.get("input", "")
            calls.append((command, prompt))
            if adapter.LANE_PLAN_MARK in prompt:
                self.assertNotIn("--resume", command)
                plan = {"lanes": [{"name": "backend", "task": "add the condition"},
                                  {"name": "frontend", "task": "add the input"}]}
                return Completed(envelope("success", json.dumps(plan), "lead-sid", 0.2, 4))
            if adapter.LANE_INTEGRATE_MARK in prompt:
                self.assertIn("--resume", command)  # the lead keeps its own context
                self.assertEqual(command[command.index("--resume") + 1], "lead-sid")
                self.assertIn("backend/A.java", prompt)  # lane reports reach the lead
                result = {"summary": "integrated", "questions": [], "design": "## Tests First\nok\n"}
                return Completed(envelope("success", json.dumps(result), "lead-sid", 0.3, 6))
            for name in ("backend", "frontend", "sql"):
                if adapter.LANE_MARK.format(name=name) in prompt:
                    self.assertIn("Only create or modify files under", prompt)
                    files = ["backend/A.java"] if name == "backend" else ["frontend/src/x.vue"]
                    result = {"summary": f"{name} done", "files": files, "questions": []}
                    return Completed(envelope("success", json.dumps(result), f"sid-{name}", 0.5, 10))
            raise AssertionError("unexpected prompt")

        with patch.object(adapter.subprocess, "run", fake_run), \
                patch.object(adapter.shutil, "which", return_value="/usr/bin/claude"), \
                patch("sys.stdin", io.StringIO(json.dumps(self._request()))), \
                patch("sys.stdout", new_callable=io.StringIO) as out, \
                patch("sys.stderr", new_callable=io.StringIO):
            code = adapter.main_with_args(self.LANES)
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 4)  # plan + 2 lanes + integrate; the sql lane had no work
        result = json.loads(out.getvalue())
        self.assertEqual(set(result) - {"_telemetry"}, {"summary", "questions", "design"})
        self.assertEqual(result["_telemetry"]["lanes"], 2)
        self.assertAlmostEqual(result["_telemetry"]["cost_usd"], 0.2 + 0.5 + 0.5 + 0.3)

    def test_a_single_lane_falls_back_to_one_ordinary_session(self):
        calls = []

        def fake_run(command, **kwargs):
            prompt = kwargs.get("input", "")
            calls.append(prompt)
            if adapter.LANE_PLAN_MARK in prompt:
                return Completed(envelope("success", json.dumps({"lanes": [{"name": "backend", "task": "all"}]}),
                                          "lead-sid", 0.1, 2))
            result = {"summary": "done", "questions": [], "design": "## Tests First\nok\n"}
            return Completed(envelope("success", json.dumps(result), "one", 0.9, 20))

        with patch.object(adapter.subprocess, "run", fake_run), \
                patch.object(adapter.shutil, "which", return_value="/usr/bin/claude"), \
                patch("sys.stdin", io.StringIO(json.dumps(self._request()))), \
                patch("sys.stdout", new_callable=io.StringIO) as out, \
                patch("sys.stderr", new_callable=io.StringIO):
            code = adapter.main_with_args(self.LANES)
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        self.assertNotIn(adapter.LANE_MARK.format(name="backend"), calls[1])
        self.assertEqual(json.loads(out.getvalue())["summary"], "done")

    def test_lanes_are_off_without_a_plan_or_without_the_flag(self):
        for argv, request in ([], self._request()), (self.LANES, self._request(plan=False)):
            calls = []

            def fake_run(command, **kwargs):
                calls.append(kwargs.get("input", ""))
                result = {"summary": "done", "questions": [], "design": "## Tests First\nok\n"}
                return Completed(envelope("success", json.dumps(result), "one", 0.9, 20))

            with patch.object(adapter.subprocess, "run", fake_run), \
                    patch.object(adapter.shutil, "which", return_value="/usr/bin/claude"), \
                    patch("sys.stdin", io.StringIO(json.dumps(request))), \
                    patch("sys.stdout", new_callable=io.StringIO), \
                    patch("sys.stderr", new_callable=io.StringIO):
                code = adapter.main_with_args(argv)
            self.assertEqual(code, 0)
            self.assertEqual(len(calls), 1)
            self.assertNotIn(adapter.LANE_PLAN_MARK, calls[0])

    def test_a_failing_lane_fails_the_implement_call(self):
        def fake_run(command, **kwargs):
            prompt = kwargs.get("input", "")
            if adapter.LANE_PLAN_MARK in prompt:
                plan = {"lanes": [{"name": "backend", "task": "a"}, {"name": "frontend", "task": "b"}]}
                return Completed(envelope("success", json.dumps(plan), "lead-sid", 0.1, 2))
            if adapter.LANE_MARK.format(name="frontend") in prompt:
                return Completed(envelope("error_during_execution", "boom", "sid-f", 0.1, 1), returncode=1)
            return Completed(envelope("success", json.dumps({"summary": "ok", "files": [], "questions": []}),
                                      "sid-b", 0.1, 2))

        with patch.object(adapter.subprocess, "run", fake_run), \
                patch.object(adapter.shutil, "which", return_value="/usr/bin/claude"), \
                patch("sys.stdin", io.StringIO(json.dumps(self._request()))), \
                patch("sys.stdout", new_callable=io.StringIO), \
                patch("sys.stderr", new_callable=io.StringIO) as err:
            code = adapter.main_with_args(self.LANES)
        self.assertEqual(code, 1)
        self.assertIn("frontend", err.getvalue())


class ClaudeProfileTests(unittest.TestCase):
    def test_legacy_command_config_preserves_telemetry_field_set(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            config = load_json(root / '.storyloop/config.json')
            driver = Path(__file__).parent / 'fixtures/fake_cli/claude_driver.py'
            config.update(adapter='command', agent_command=[sys.executable, str(driver.resolve())])
            self.assertNotIn('adapters', config)
            self.assertNotIn('roles', config)
            write_json(root / '.storyloop/config.json', config)
            write_json(root / 'scenario.json', {'result': {'summary': 'done', 'questions': []}})
            result = invoke_project_agent(root, 'implement', {'change_id': 'FEAT-1'})
            self.assertEqual(result, {'summary': 'done', 'questions': []})
            self.assertEqual(set(ledger_entries(root, 'FEAT-1')[0]),
                             {'lessons_provided', 'lessons_claimed', 'lessons_unknown', 'lessons_truncated',
                              'role', 'wall_ms', 'at', 'call', 'skills', 'model', 'cost_usd',
                              'turns', 'duration_ms', 'input_tokens', 'output_tokens', 'segments'})
            prompt = load_json(root / 'cli-request.json')['prompt']
            self.assertNotIn('role_profile', json.loads(prompt.split('Request:\n')[-1]))

    def test_handshake_never_starts_model_and_rejects_invalid_mapping(self):
        request = {'protocol': 'storyloop.adapter-capabilities.v1'}
        with patch.object(adapter.subprocess, 'run') as run, \
                patch('sys.stdin', io.StringIO(json.dumps(request))), \
                patch('sys.stdout', new_callable=io.StringIO) as out:
            code = adapter.main_with_args(['--tier-model', 'standard=small'])
        self.assertEqual(code, 0)
        run.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())['tiers'], ['standard'])
        with patch('sys.stdin', io.StringIO(json.dumps(request))), \
                patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(adapter.main_with_args(['--tier-model', 'strong=']), 2)

    def test_unmapped_legacy_model_does_not_claim_tier(self):
        request = {'role': 'implement', 'project': '/tmp/p', 'payload': {},
                   'role_profile': {'tier': 'strong', 'subagents': {}}, 'instructions': 'implement'}
        reply = Completed(envelope('success', '{"summary":"done","questions":[]}', 'sid', 0.1, 1))
        with patch.object(adapter.subprocess, 'run', return_value=reply) as run, \
                patch.object(adapter.shutil, 'which', return_value='/usr/bin/claude'), \
                patch('sys.stdin', io.StringIO(json.dumps(request))), \
                patch('sys.stdout', new_callable=io.StringIO) as out, \
                patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(adapter.main_with_args(['--model', 'legacy-model']), 0)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index('--model') + 1], 'legacy-model')
        telemetry = json.loads(out.getvalue())['_telemetry']
        self.assertEqual(telemetry['vendor'], 'anthropic')
        self.assertNotIn('tier_applied', telemetry)


if __name__ == "__main__":
    unittest.main()
