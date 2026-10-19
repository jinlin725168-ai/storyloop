import json
from pathlib import Path
import sys
import tempfile
import unittest

from storyloop.baseline import bootstrap_project, load_project_config
from storyloop.models import StoryLoopError
from storyloop.project_agents import invoke_project_agent
from storyloop.storage import write_json
from storyloop.telemetry import ledger_entries
from tests.test_baseline_behavior import git, repository


class ProjectAgentTests(unittest.TestCase):
    def test_legacy_request_and_ledger_have_no_profile_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            config = load_project_config(root)
            config["adapter"] = "manual"
            write_json(root / ".storyloop/config.json", config)

            with self.assertRaisesRegex(StoryLoopError, "Manual input"):
                invoke_project_agent(root, "product", {"idea": "orders"})

            request = json.loads((root / ".storyloop/requests/project-product.json").read_text(encoding="utf-8"))
            self.assertEqual(set(request) - {"request_id", "instructions"},
                             {"protocol", "role", "project", "payload", "response_schema"})
            self.assertNotIn("role_profile", request)
            entry = ledger_entries(root, "pending")[0]
            self.assertEqual(set(entry) - {"at", "call", "wall_ms"}, {"role", "skills"})
    def test_role_adapter_request_contains_profile_and_engine_ledger_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            script = root / "agent.py"
            script.write_text(
                "import json, sys\nrequest=json.load(sys.stdin)\n"
                "json.dump(request, open(sys.argv[1], 'w'))\n"
                "print(json.dumps({'verdict':'pass','summary':'ok','questions':[],'findings':[],'coverage':[], '_telemetry': {'tier_requested':'bad','adapter':'bad'}}))\n",
                encoding="utf-8")
            seen = root / "seen.json"
            config = load_project_config(root)
            config.update({"adapters": {"reviewer": {"kind": "command", "agent_command": [sys.executable, str(script), str(seen)]}},
                           "roles": {"default_adapter": "reviewer", "review": {"adapter": "reviewer"}}})
            write_json(root / ".storyloop/config.json", config)

            invoke_project_agent(root, "review", {"change": {"change_id": "FEAT-1"}})

            request = json.loads(seen.read_text(encoding="utf-8"))
            self.assertEqual(request["role_profile"]["tier"], "strong")
            entry = ledger_entries(root, "FEAT-1")[0]
            self.assertEqual(entry["tier_requested"], "strong")
            self.assertEqual(entry["adapter"], "reviewer")
    def test_implement_request_preserves_legacy_roots_under_plan_and_green_locks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / "app").mkdir()
            (root / "app/cli.py").write_text("VALUE = 0\n", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests/test_cli.py").write_text("pass\n", encoding="utf-8")
            git(root, "add", "app", "tests")
            git(root, "commit", "-q", "-m", "app")
            bootstrap_project(root, {"module_roots": ["app"], "test_roots": ["tests"]})
            config = load_project_config(root)
            config["adapter"] = "manual"
            write_json(root / ".storyloop/config.json", config)

            with self.assertRaisesRegex(StoryLoopError, "Manual input"):
                invoke_project_agent(root, "implement", {
                    "change": {"change_id": "FEAT-1"},
                    "design_plan": {"forbidden_paths": ["tests"], "constraints": []},
                    "characterization": {
                        "status": "GREEN", "tests": ["tests/test_cli.py"], "locks": {},
                    },
                })
            request = json.loads(
                (root / ".storyloop/requests/project-implement.json").read_text(encoding="utf-8")
            )
            instructions = request["instructions"]
            self.assertIn("both existing and new files under the configured module roots and test roots",
                          instructions)
            self.assertIn(".storyloop/config.json", instructions)
            self.assertIn("integration path or foundation file", instructions)
            self.assertIn("existing non-test file outside its `touched` list", instructions)
            self.assertNotIn("Modify only new files", instructions)
            self.assertIn("structured approved plan is authoritative", instructions)
            self.assertIn("forbidden_paths", instructions)
            self.assertIn("GREEN characterization artifacts", instructions)
            self.assertIn("run the existing approved checks", instructions)
            self.assertIn("never invent a test or a failing result", instructions)

    def test_manual_agent_response_is_bound_to_exact_request_and_consumed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            config = load_project_config(root)
            config["adapter"] = "manual"
            write_json(root / ".storyloop/config.json", config)

            with self.assertRaisesRegex(StoryLoopError, "Manual input"):
                invoke_project_agent(root, "product", {"idea": "orders"})
            request_path = root / ".storyloop/requests/project-product.json"
            request = json.loads(request_path.read_text(encoding="utf-8"))
            response_path = root / ".storyloop/responses/project-product.json"
            write_json(response_path, {
                "request_id": request["request_id"],
                "result": {"version": 1, "goal": "g", "non_goals": [], "questions": [], "stories": []},
            })

            result = invoke_project_agent(root, "product", {"idea": "orders"})

            self.assertEqual(result["goal"], "g")
            self.assertFalse(response_path.exists())

    def test_unknown_role_fails_before_adapter_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            with self.assertRaisesRegex(StoryLoopError, "role"):
                invoke_project_agent(root, "owner", {})


if __name__ == "__main__":
    unittest.main()
