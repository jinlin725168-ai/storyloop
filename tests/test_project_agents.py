import json
from pathlib import Path
import tempfile
import unittest

from storyloop.baseline import bootstrap_project, load_project_config
from storyloop.models import StoryLoopError
from storyloop.project_agents import invoke_project_agent
from storyloop.storage import write_json
from tests.test_baseline_behavior import repository


class ProjectAgentTests(unittest.TestCase):
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
