"""A command adapter gets one repair round for an answer that breaks the role schema.

2026-11-17 L3 on sqlite-utils: the Claude command adapter merged review perspectives into
findings without tracking fields; the run stopped at AGENT_CONTRACT_INVALID with no repair
and no recovery option, while the Codex transport already repairs schema errors.
"""

import json
from pathlib import Path
import sys
import tempfile
import unittest

from storyloop import agent_transport as agents
from storyloop.models import StoryLoopError
from storyloop.role_schemas import schema_for

AGENT = """
import json, sys
request = json.load(sys.stdin)
with open(sys.argv[1], "a", encoding="utf-8") as log:
    log.write(json.dumps(request["payload"]) + "\\n")
fixed = bool(request["payload"].get("validation_errors"))
answer = ({"summary": "done", "questions": [], "design": "## Tests First\\nok"}
          if fixed and sys.argv[2] == "fixes" else {"summary": "missing fields"})
print(json.dumps({**answer, "_telemetry": {"cost_usd": 0.1, "turns": 2}}))
"""


class CommandSchemaRepairTests(unittest.TestCase):
    def invoke(self, mode):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        (root / ".storyloop").mkdir()
        script, log = root / "agent.py", root / "requests.log"
        script.write_text(AGENT, encoding="utf-8")
        config = {"kind": "command", "agent_command": [sys.executable, str(script), str(log), mode],
                  "agent_timeout_seconds": 60}
        request = {"project": str(root), "role": "implement", "payload": {"change": {}}}
        try:
            return agents.invoke(request, config, schema_for("implement", request["payload"]),
                                 {"instructions": "", "stderr": "", "raw": ""}), log
        except StoryLoopError as error:
            return error, log

    def test_schema_violation_gets_one_repair_with_the_errors(self):
        result, log = self.invoke("fixes")
        self.assertEqual(result["summary"], "done")
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(calls), 2)
        self.assertNotIn("validation_errors", calls[0])
        self.assertTrue(calls[1]["validation_errors"])
        self.assertEqual(calls[1]["previous_response"]["summary"], "missing fields")
        self.assertEqual((result["_telemetry"]["cost_usd"], result["_telemetry"]["turns"]), (0.2, 4))

    def test_answer_still_invalid_after_repair_is_contract_invalid(self):
        error, log = self.invoke("stays")
        self.assertIsInstance(error, StoryLoopError)
        self.assertEqual(error.code, "AGENT_CONTRACT_INVALID")
        self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
