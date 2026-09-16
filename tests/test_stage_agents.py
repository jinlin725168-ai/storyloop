"""Tests for selected-only v2 agent invocation."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storyloop.models import StoryLoopError
from storyloop.processutil import _environment
from storyloop.stage_agents import (
    MAX_RESPONSE_BYTES, adapter_control_root, consume_manual_ack,
    invoke_selected_adapter, probe_selected_adapter,
)
from tests.test_stage_config import loop_config


def response_for(request: dict, status: str = "ok") -> dict:
    return {
        "protocol": "loop.agent.v2",
        "request_id": request["request_id"],
        "status": status,
        "summary": "done",
        "questions": ["choose"] if status != "ok" else [],
    }


def process_result(stdout: str = "", **changes) -> dict:
    result = {
        "returncode": 0,
        "stdout": stdout,
        "stderr": "",
        "timed_out": False,
        "truncated": False,
        "error": "",
        "contained": True,
    }
    result.update(changes)
    return result


class StageAgentTests(unittest.TestCase):
    def test_process_environment_forwards_only_allowlisted_nonsensitive_names(self):
        source = {"PATH": "allowed", "JAVA_HOME": "java", "UNLISTED": "hidden",
                  "API_KEY": "secret"}

        result = _environment(["PATH", "JAVA_HOME", "API_KEY"], source)

        self.assertEqual(result["PATH"], "allowed")
        self.assertEqual(result["JAVA_HOME"], "java")
        self.assertNotIn("UNLISTED", result)
        self.assertNotIn("API_KEY", result)

    def test_probe_uses_existing_precedence_and_never_touches_unselected_adapter(self):
        config = loop_config()
        calls = []

        def resolve(name):
            calls.append(name)
            return f"C:/tools/{name}.exe"

        selected = probe_selected_adapter(
            config, requested="manual", environ={"LOOP_AGENT": "claude"}, resolver=resolve
        )

        self.assertEqual(selected, {"name": "manual", "kind": "manual", "command": []})
        self.assertEqual(calls, [])

    def test_probe_resolves_only_the_selected_command_to_a_native_executable(self):
        config = loop_config()
        config["agent"]["adapters"]["other"] = {
            "kind": "command", "command": ["missing", "--json"]
        }
        calls = []

        selected = probe_selected_adapter(
            config, requested="claude", resolver=lambda name: calls.append(name) or "C:/claude.exe"
        )

        self.assertEqual(selected["command"], ["C:/claude.exe", "--json"])
        self.assertEqual(calls, ["claude"])

    def test_command_receives_strict_protocol_and_allowlisted_environment(self):
        config = loop_config()
        captured = {}

        def run(command, cwd, timeout, **kwargs):
            captured.update(command=command, cwd=cwd, timeout=timeout, **kwargs)
            request = json.loads(kwargs["input_text"])
            return process_result(json.dumps(response_for(request)))

        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.stage_agents.run_process", side_effect=run):
            result = invoke_selected_adapter(
                Path(folder), config, "implement", "foundation", {"goal": "unicode-需求"},
                requested="claude", environ={"PATH": "safe", "UNLISTED": "hidden"},
                resolver=lambda name: "C:/claude.exe",
            )

        request = json.loads(captured["input_text"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(request["protocol"], "loop.agent.v2")
        self.assertEqual(request["adapter"], "claude")
        self.assertEqual(request["stage"], "foundation")
        self.assertEqual(captured["environment_allowlist"], ["PATH", "JAVA_HOME"])
        self.assertEqual(captured["source_environment"], {"PATH": "safe", "UNLISTED": "hidden"})
        self.assertNotIn("safe", captured["input_text"])
        self.assertNotIn("hidden", captured["input_text"])

    def test_containment_is_checked_before_untrusted_output(self):
        config = loop_config()
        unsafe = process_result("not-json", contained=False)

        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.stage_agents.run_process", return_value=unsafe):
            with self.assertRaisesRegex(StoryLoopError, "securely contained") as caught:
                invoke_selected_adapter(
                    Path(folder), config, "implement", "foundation", {},
                    requested="claude", resolver=lambda name: "C:/claude.exe",
                )
        self.assertEqual(caught.exception.code, "UNSAFE_CONTAINMENT")

    def test_external_process_failures_are_safe_and_deterministic(self):
        config = loop_config()
        cases = [
            (process_result(timed_out=True), "time budget"),
            (process_result(truncated=True), "output limit"),
            (process_result(returncode=7, stderr="TOP_SECRET"), "process failed"),
            (process_result(error="TOP_SECRET"), "process failed"),
        ]
        for process, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as folder, \
                    patch("storyloop.stage_agents.run_process", return_value=process):
                with self.assertRaisesRegex(StoryLoopError, expected) as caught:
                    invoke_selected_adapter(
                        Path(folder), config, "implement", "foundation", {},
                        requested="claude", resolver=lambda name: "C:/claude.exe",
                    )
                self.assertNotIn("TOP_SECRET", str(caught.exception))

    def test_response_rejects_unknown_fields_and_request_mismatch(self):
        config = loop_config()

        def run_unknown(command, cwd, timeout, **kwargs):
            request = json.loads(kwargs["input_text"])
            response = {**response_for(request), "verdict": "pass"}
            return process_result(json.dumps(response))

        def run_stale(command, cwd, timeout, **kwargs):
            request = json.loads(kwargs["input_text"])
            response = {**response_for(request), "request_id": "0" * 64}
            return process_result(json.dumps(response))

        for run, expected in ((run_unknown, "Unknown contract fields"),
                              (run_stale, "current request")):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as folder, \
                    patch("storyloop.stage_agents.run_process", side_effect=run):
                with self.assertRaisesRegex(StoryLoopError, expected):
                    invoke_selected_adapter(
                        Path(folder), config, "review", "foundation", {},
                        requested="claude", resolver=lambda name: "C:/claude.exe",
                    )

    def test_manual_response_is_bound_to_request_and_consumed_once(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            with self.assertRaises(StoryLoopError) as missing:
                invoke_selected_adapter(project, config, "implement", "foundation", {},
                                        requested="manual")
            self.assertEqual(missing.exception.code, "NEEDS_INPUT")
            request_path = adapter_control_root(project) / "requests" / "foundation.implement.json"
            response_path = adapter_control_root(project) / "responses" / "foundation.implement.json"
            self.assertNotIn(project.resolve(), request_path.parents)
            request = json.loads(request_path.read_text(encoding="utf-8"))
            response_path.parent.mkdir(parents=True)
            response_path.write_text(json.dumps({
                "request_id": request["request_id"], "result": response_for(request)
            }), encoding="utf-8")

            result = invoke_selected_adapter(project, config, "implement", "foundation", {},
                                             requested="manual")

            self.assertEqual(result["status"], "ok")
            self.assertTrue(response_path.exists())
            consume_manual_ack(project, result["_manual_ack"])
            self.assertFalse(response_path.exists())
            consume_manual_ack(project, result["_manual_ack"])

    def test_stale_manual_response_and_unknown_context_fail_closed(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            response_path = adapter_control_root(project) / "responses" / "foundation.review.json"
            response_path.parent.mkdir(parents=True)
            response_path.write_text(json.dumps({"request_id": "stale", "result": {}}),
                                     encoding="utf-8")
            with self.assertRaises(StoryLoopError) as stale:
                invoke_selected_adapter(project, config, "review", "foundation", {},
                                        requested="manual")
            self.assertEqual(stale.exception.code, "NEEDS_INPUT")
            self.assertTrue(response_path.exists())

        with self.assertRaisesRegex(StoryLoopError, "Unknown agent role"):
            invoke_selected_adapter(Path.cwd(), config, "judge", "foundation", {},
                                    requested="manual")
        with self.assertRaisesRegex(StoryLoopError, "Unknown stage"):
            invoke_selected_adapter(Path.cwd(), config, "review", "missing", {},
                                    requested="manual")

    def test_malformed_manual_response_remains_recoverable(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            response_path = adapter_control_root(project) / "responses" / "foundation.review.json"
            response_path.parent.mkdir(parents=True)
            response_path.write_text("not-json", encoding="utf-8")

            with self.assertRaises(StoryLoopError) as caught:
                invoke_selected_adapter(project, config, "review", "foundation", {},
                                        requested="manual")

            self.assertEqual(caught.exception.code, "NEEDS_INPUT")
            self.assertTrue(response_path.exists())

    def test_nonterminal_manual_response_keeps_questions_and_receipt(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            with self.assertRaises(StoryLoopError):
                invoke_selected_adapter(project, config, "review", "foundation", {},
                                        requested="manual")
            request_path = adapter_control_root(project) / "requests" / "foundation.review.json"
            response_path = adapter_control_root(project) / "responses" / "foundation.review.json"
            self.assertNotIn(project.resolve(), request_path.parents)
            request = json.loads(request_path.read_text(encoding="utf-8"))
            response_path.parent.mkdir(parents=True)
            response_path.write_text(json.dumps({
                "request_id": request["request_id"],
                "result": response_for(request, "needs_input"),
            }), encoding="utf-8")

            with self.assertRaises(StoryLoopError) as caught:
                invoke_selected_adapter(project, config, "review", "foundation", {},
                                        requested="manual")

            self.assertEqual(caught.exception.code, "NEEDS_INPUT")
            self.assertEqual(caught.exception.details["questions"], ["choose"])
            self.assertTrue(response_path.exists())

    def test_manual_response_is_size_checked_before_json_loading(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            response_path = adapter_control_root(project) / "responses" / "foundation.review.json"
            response_path.parent.mkdir(parents=True)
            response_path.write_text("x" * (MAX_RESPONSE_BYTES + 1), encoding="utf-8")

            with self.assertRaises(StoryLoopError) as caught:
                invoke_selected_adapter(project, config, "review", "foundation", {},
                                        requested="manual")

            self.assertEqual(caught.exception.code, "NEEDS_INPUT")
            self.assertIn("bounded", str(caught.exception))

    def test_protocol_rejects_nonfinite_payload_and_duplicate_response_fields(self):
        config = loop_config()
        with patch("storyloop.stage_agents.run_process") as run:
            with self.assertRaisesRegex(StoryLoopError, "bounded JSON"):
                invoke_selected_adapter(
                    Path.cwd(), config, "implement", "foundation", {"value": float("nan")},
                    requested="claude", resolver=lambda name: "C:/claude.exe",
                )
            run.assert_not_called()

        def duplicate(command, cwd, timeout, **kwargs):
            request = json.loads(kwargs["input_text"])
            output = (f'{{"protocol":"loop.agent.v2","request_id":"{request["request_id"]}",'
                      '"status":"ok","status":"needs_input","summary":"x",'
                      '"questions":["choose"]}}')
            return process_result(output)

        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.stage_agents.run_process", side_effect=duplicate):
            with self.assertRaisesRegex(StoryLoopError, "duplicate"):
                invoke_selected_adapter(
                    Path(folder), config, "review", "foundation", {}, requested="claude",
                    resolver=lambda name: "C:/claude.exe",
                )

    def test_payload_must_be_bounded_json_without_starting_an_adapter(self):
        config = loop_config()
        for payload in ({"bad": object()}, {"huge": "x" * 2_000_000}):
            with self.subTest(kind=type(next(iter(payload.values()))).__name__), \
                    patch("storyloop.stage_agents.run_process") as run:
                with self.assertRaisesRegex(StoryLoopError, "bounded JSON"):
                    invoke_selected_adapter(
                        Path.cwd(), config, "implement", "foundation", payload,
                        requested="claude", resolver=lambda name: "C:/claude.exe",
                    )
                run.assert_not_called()

    def test_architecture_and_input_statuses_are_machine_readable(self):
        config = loop_config()

        def run_status(status):
            def run(command, cwd, timeout, **kwargs):
                request = json.loads(kwargs["input_text"])
                return process_result(json.dumps(response_for(request, status)))
            return run

        for status, code in (("architecture_decision_required", "ARCHITECTURE_DECISION_REQUIRED"),
                             ("needs_input", "NEEDS_INPUT")):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as folder, \
                    patch("storyloop.stage_agents.run_process", side_effect=run_status(status)):
                with self.assertRaises(StoryLoopError) as caught:
                    invoke_selected_adapter(
                        Path(folder), config, "implement", "foundation", {},
                        requested="claude", resolver=lambda name: "C:/claude.exe",
                    )
                self.assertEqual(caught.exception.code, code)

    def test_agent_process_uses_the_shared_absolute_deadline(self):
        config = loop_config()
        captured = {}

        def run(command, cwd, timeout, **kwargs):
            captured.update(timeout=timeout, **kwargs)
            request = json.loads(kwargs["input_text"])
            return process_result(json.dumps(response_for(request)))

        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.stage_agents.run_process", side_effect=run):
            invoke_selected_adapter(
                Path(folder), config, "implement", "foundation", {},
                requested="claude", resolver=lambda name: "C:/claude.exe",
                attempt_deadline_ms=2_250, budget_clock_ms=lambda: 250,
            )

        self.assertEqual(captured["timeout"], 2.0)
        self.assertEqual(captured["absolute_deadline"], 2.25)
    def test_codex_uses_same_protocol_and_requires_containment_before_response_file(self):
        config = loop_config()
        captured = {}

        def run(command, cwd, timeout, **kwargs):
            captured.update(command=command, **kwargs)
            request = json.loads(kwargs["input_text"])
            output = Path(command[command.index("--output-last-message") + 1])
            output.write_text(json.dumps(response_for(request)), encoding="utf-8")
            return process_result()

        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.stage_agents.run_process", side_effect=run):
            project = Path(folder) / "project"
            project.mkdir()
            result = invoke_selected_adapter(
                project, config, "review", "foundation", {}, requested="codex",
                resolver=lambda name: "C:/codex.exe",
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(json.loads(captured["input_text"])["protocol"], "loop.agent.v2")
        self.assertIn("--output-last-message", captured["command"])
        output = Path(captured["command"][captured["command"].index("--output-last-message") + 1])
        self.assertNotIn(project.resolve(), output.parents)


if __name__ == "__main__":
    unittest.main()
