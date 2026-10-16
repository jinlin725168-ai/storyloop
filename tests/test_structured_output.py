"""Role schemas are enforced natively by the agent CLIs and repaired in the same session."""

from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project, load_project_config
from tests.routing_support import legacy_create_change as create_change
from storyloop.models import StoryLoopError
from storyloop.project_agents import conform, invoke_project_agent
from storyloop.project_loop import LoopServices, run_change
from storyloop.role_schemas import schema_for
from storyloop.storage import write_json
from tests.support import product
from tests.test_baseline_behavior import repository
from tests.test_claude_adapter import Completed, adapter, envelope
from tests.test_codex_adapter import THREAD, FakeCodex, project as codex_project
from tests.test_project_loop import NOW, smoke_payload


def codex_call(root: Path, messages: list[str], role: str = "implement", payload: dict | None = None):
    fake = FakeCodex(messages)
    with patch("storyloop.project_agents.run_process", fake), \
            patch("storyloop.project_agents._codex_executable", return_value=["codex"]):
        result = invoke_project_agent(root, role, payload or {"change": {"change_id": "FEAT-9"}})
    return result, fake


def command_project(root: Path, script: str) -> None:
    repository(root)
    bootstrap_project(root)
    config = load_project_config(root)
    config.update({"adapter": "command", "agent_command": [sys.executable, "-c", script]})
    write_json(root / ".storyloop/config.json", config)


def echo_script(answer: dict) -> str:
    """A command adapter that fails unless the request carries the role schema, then answers."""
    return ("import json,sys; request=json.load(sys.stdin); "
            "sys.stderr.write('schema keys: ' + ','.join(request['response_schema']['required']) + '\\n'); "
            f"print(json.dumps({answer!r}))")


class CodexStructuredOutputTests(unittest.TestCase):
    def test_role_schema_is_enforced_natively_on_every_codex_invocation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            codex_project(root)
            seen = []
            fake = FakeCodex(['{"summary": "done", "questions": [], "design": null}'])

            def capture(command, cwd, timeout, input_text=None, **kwargs):
                schema_path = Path(command[command.index("--output-schema") + 1])
                seen.append(json.loads(schema_path.read_text(encoding="utf-8")))
                return fake(command, cwd, timeout, input_text=input_text, **kwargs)

            with patch("storyloop.project_agents.run_process", capture), \
                    patch("storyloop.project_agents._codex_executable", return_value=["codex"]):
                result = invoke_project_agent(root, "implement", {"change": {"change_id": "FEAT-9"}})
            self.assertEqual(result, {"summary": "done", "questions": []})  # nullable fields dropped
            self.assertEqual(seen[0]["required"], ["summary", "questions", "design", "facts"])
            self.assertEqual(list(root.glob(".storyloop/schema-*")), [])

    def test_a_legacy_answer_may_omit_nullable_facts(self):
        legacy = {"summary": "done", "questions": [], "design": None}
        normalized, errors = conform(legacy, schema_for("implement", {}))
        self.assertEqual(errors, [])
        self.assertEqual(normalized, {"summary": "done", "questions": []})

    def test_a_schema_mismatch_is_repaired_in_the_same_thread_with_the_concrete_problems(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            codex_project(root)
            result, fake = codex_call(root, ['{"summary": "done", "questions": [], "notes": "x"}',
                                             '{"summary": "done", "questions": []}'])
            self.assertEqual(result["summary"], "done")
            resume_command, resume_prompt = fake.calls[1]
            self.assertEqual(resume_command[1:4], ["exec", "resume", THREAD])
            self.assertIn("--output-schema", resume_command)
            self.assertIn("unknown field 'notes'", resume_prompt)

    def test_a_mismatch_that_survives_the_repairs_is_a_contract_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            codex_project(root)
            wrong = '{"summary": 1, "questions": []}'
            with self.assertRaises(StoryLoopError) as caught:
                codex_call(root, [wrong, wrong, wrong])
            self.assertEqual(caught.exception.code, "AGENT_CONTRACT_INVALID")
            self.assertIn("$.summary: expected string, got integer", caught.exception.details["schema_errors"])


class CommandAdapterContractTests(unittest.TestCase):
    def test_the_request_carries_the_role_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            command_project(root, echo_script({"summary": "ok", "questions": [], "design": None}))
            result = invoke_project_agent(root, "implement", {"change": {"change_id": "FEAT-3"}})
            self.assertEqual(result, {"summary": "ok", "questions": []})

    def test_an_answer_off_the_schema_is_refused_with_its_problems(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            command_project(root, echo_script({"verdict": "ok", "summary": "s", "findings": [], "questions": []}))
            with self.assertRaisesRegex(StoryLoopError, "verdict: must be one of") as caught:
                invoke_project_agent(root, "review", {"change": {"change_id": "FEAT-3"}})
            self.assertEqual(caught.exception.code, "AGENT_CONTRACT_INVALID")


class ClaudeStructuredOutputTests(unittest.TestCase):
    def test_the_final_session_uses_json_schema_and_reports_its_session(self):
        calls = []
        reply = json.loads(envelope("success", "Done, all good.", "sid-9", 0.2, 3))
        reply["structured_output"] = {"summary": "done", "questions": [], "design": None}

        def fake_run(command, **kwargs):
            calls.append(list(command))
            return Completed(json.dumps(reply))

        schema = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"],
                  "additionalProperties": False}
        request = {"role": "implement", "project": "/tmp/p", "payload": {}, "instructions": "do",
                   "response_schema": schema}
        with patch.object(adapter.subprocess, "run", fake_run), \
                patch.object(adapter.shutil, "which", return_value="/usr/bin/claude"), \
                patch("sys.stdin", io.StringIO(json.dumps(request))), \
                patch("sys.stdout", new_callable=io.StringIO) as out, \
                patch("sys.stderr", new_callable=io.StringIO):
            code = adapter.main_with_args([])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(calls[0][calls[0].index("--json-schema") + 1]), schema)
        result = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(result["summary"], "done")  # structured_output wins over the prose result
        self.assertEqual(result["_telemetry"]["sessions"], ["sid-9"])


def approved_change():
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    repository(root)
    bootstrap_project(root, {"test_commands": []})
    state = create_change(root, "orders", product(), now=lambda: NOW)
    approve_change(root, state["change_id"], now=lambda: NOW)
    return temporary, root, state["change_id"]


def contract_breaking_implement(attempts: list):
    """First answer breaks the schema after writing code; the second attempt finishes the work."""
    def implement(project, context):
        attempts.append(context)
        target = project / "modules/orders/service.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        if len(attempts) == 1:
            target.write_text("VALUE = 0\n", encoding="utf-8")
            raise StoryLoopError("Agent answer does not match the role's response schema: $.summary: "
                                 "expected string, got integer", code="AGENT_CONTRACT_INVALID")
        target.write_text("VALUE = 1\n", encoding="utf-8")
        return {"summary": "fixed", "questions": []}
    return implement


def passing_review(project, context):
    return {"verdict": "pass", "summary": "ok", "findings": [], "questions": []}


class LoopContractTests(unittest.TestCase):
    def test_a_malformed_implement_answer_costs_an_attempt_instead_of_aborting_the_run(self):
        temporary, root, change_id = approved_change()
        attempts = []
        services = LoopServices(generate_smoke=lambda project, state: smoke_payload(change_id),
                                implement=contract_breaking_implement(attempts), review=passing_review)
        try:
            result = run_change(root, change_id, services)
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(len(attempts), 2)
            self.assertEqual(attempts[1]["failures"][0]["contract"]["status"], "INVALID")
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
