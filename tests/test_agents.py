import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        (self.project / "workspace").mkdir()
        (self.project / ".storyloop").mkdir()
        self.config = {"version": 1, "adapter": "command", "agent_command": [],
                       "max_attempts": 3, "max_seconds": 30,
                       "agent_timeout_seconds": 3, "check_timeout_seconds": 3,
                       "repeated_failure_limit": 2}

    def invoke(self, role="product", payload=None):
        self.assertIsNotNone(importlib.util.find_spec("storyloop.agents"),
                             "agent adapter module must exist")
        module = importlib.import_module("storyloop.agents")
        (self.project / "config.json").write_text(json.dumps(self.config), encoding="utf-8")
        return module.invoke(self.project, role, payload or {})

    def test_command_receives_role_and_workspace_as_structured_input(self):
        self.config["agent_command"] = [sys.executable, "-c",
            "import sys,json,os; r=json.load(sys.stdin); "
            "print(json.dumps({'role':r['role'],'cwd':os.getcwd(),'payload':r['payload']}))"]
        value = self.invoke(payload={"message": "hello \"quoted\" $()"})
        self.assertEqual(value["role"], "product")
        self.assertEqual(Path(value["cwd"]).resolve(), (self.project / "workspace").resolve())
        self.assertEqual(value["payload"]["message"], 'hello "quoted" $()')

    def test_nonzero_and_malformed_output_never_become_success(self):
        for script in ["import sys; sys.stderr.write('private-output'); sys.exit(2)",
                       "print('not-json')", "print('[]')"]:
            with self.subTest(script=script):
                self.config["agent_command"] = [sys.executable, "-c", script]
                try:
                    self.invoke()
                except Exception as exc:
                    self.assertNotIn("private-output", str(exc))
                    self.assertNotIsInstance(exc, AssertionError)
                else:
                    self.fail("invalid adapter result must be rejected")

    def test_manual_requires_matching_response_and_consumes_it(self):
        self.config["adapter"] = "manual"
        try:
            self.invoke(payload={"message": "first"})
        except Exception as exc:
            self.assertNotIsInstance(exc, AssertionError)
        request_file = self.project / ".storyloop" / "requests" / "product.json"
        self.assertTrue(request_file.exists(), "manual adapter must persist request")
        request = json.loads(request_file.read_text(encoding="utf-8"))
        response_file = self.project / ".storyloop" / "responses" / "product.json"
        response_file.parent.mkdir(exist_ok=True)
        response_file.write_text(json.dumps({"request_id": request["request_id"],
                                            "result": {"goal": "ready"}}), encoding="utf-8")
        result = self.invoke(payload={"message": "first"})
        self.assertEqual(result, {"goal": "ready"})
        self.assertFalse(response_file.exists())

    def test_unknown_role_is_rejected_before_starting_process(self):
        self.config["agent_command"] = [sys.executable, "-c", "print('{}')"]
        with self.assertRaisesRegex(Exception, "role"):
            self.invoke(role="admin")


if __name__ == "__main__":
    unittest.main()
