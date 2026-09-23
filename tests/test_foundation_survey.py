"""A survey role explores the foundation and writes a protected knowledge base that every role reads first."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

from storyloop.baseline import bootstrap_project
from storyloop.foundation_survey import (FOUNDATION_ROOT, REQUIRED_DOCUMENTS, foundation_payload,
                                         install_foundation_documents, survey_foundation)
from storyloop.models import StoryLoopError
from storyloop.project_agents import invoke_project_agent
from storyloop.storage import load_json, write_json
from tests.test_baseline_behavior import git, repository

SURVEYOR = '''
import json, sys
request = json.load(sys.stdin)
assert request["role"] == "survey", request
assert "foundation" not in request["payload"], request
docs = [{"path": name, "content": "# " + name + "\\n\\nsurveyed at baseline " + request["payload"]["baseline_version"] + "\\n"}
        for name in %s]
print(json.dumps({"documents": docs}))
'''


def _project(root: Path, extra: str = "") -> None:
    repository(root)
    (root / "README.md").write_text("# demo\\n", encoding="utf-8")
    git(root, "add", "README.md")
    git(root, "commit", "-q", "-m", "readme")
    bootstrap_project(root, {"test_commands": [[sys.executable, "-c", "pass"]]})
    script = root.parent / "surveyor.py"
    script.write_text(SURVEYOR % json.dumps(sorted(REQUIRED_DOCUMENTS) + ([extra] if extra else [])),
                      encoding="utf-8")
    config = load_json(root / ".storyloop/config.json")
    config.update({"adapter": "command", "agent_command": [sys.executable, str(script)]})
    write_json(root / ".storyloop/config.json", config)


class SurveyTests(unittest.TestCase):
    def test_survey_writes_the_knowledge_base_and_opens_a_base_proposal(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "repo"
            root.mkdir()
            _project(root, extra="glossary.md")
            result = survey_foundation(root, "first survey")
            self.assertEqual(result["status"], "SURVEYED")
            written = {Path(p).name for p in result["paths"]}
            self.assertEqual(written, set(REQUIRED_DOCUMENTS) | {"glossary.md"})
            self.assertTrue((root / FOUNDATION_ROOT / "architecture.md").is_file())
            proposal = load_json(Path(result["proposal"]))
            self.assertEqual({item["path"] for item in proposal["changes"]}, set(result["paths"]))
            self.assertTrue(all(item["category"] == "FOUNDATION_CHANGE" for item in proposal["changes"]))
            self.assertIn("base-update", result["next"])

    def test_every_role_receives_the_foundation_documents_once_they_exist(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "repo"
            root.mkdir()
            _project(root)
            config = load_json(root / ".storyloop/config.json")
            config["adapter"] = "manual"
            write_json(root / ".storyloop/config.json", config)
            with self.assertRaises(StoryLoopError):
                invoke_project_agent(root, "implement", {"change": {"change_id": "FEAT-1"}})
            request = load_json(root / ".storyloop/requests/project-implement.json")
            self.assertNotIn("foundation", request["payload"])

            install_foundation_documents(root, {"documents": [
                {"path": name, "content": f"# {name}\\n"} for name in REQUIRED_DOCUMENTS]})
            with self.assertRaises(StoryLoopError):
                invoke_project_agent(root, "implement", {"change": {"change_id": "FEAT-1"}})
            request = load_json(root / ".storyloop/requests/project-implement.json")
            foundation = request["payload"]["foundation"]
            self.assertEqual(foundation["version"], "1.0.0")
            self.assertEqual(foundation["documents"], sorted(f"{FOUNDATION_ROOT}/{n}" for n in REQUIRED_DOCUMENTS))
            self.assertEqual(foundation_payload(root), foundation)

    def test_install_rejects_incomplete_unsafe_or_secret_bearing_documents(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "repo"
            root.mkdir()
            _project(root)
            complete = [{"path": name, "content": f"# {name}\\n"} for name in REQUIRED_DOCUMENTS]
            with self.assertRaises(StoryLoopError):
                install_foundation_documents(root, {"documents": complete[:-1]})
            with self.assertRaises(StoryLoopError):
                install_foundation_documents(root, {"documents": complete + [{"path": "../x.md", "content": "x"}]})
            with self.assertRaises(StoryLoopError):
                install_foundation_documents(root, {"documents": complete + [{"path": "notes.txt", "content": "x"}]})
            leaking = [dict(item) for item in complete]
            leaking[0]["content"] = "spring.datasource.password=hunter2\\n"
            with self.assertRaises(StoryLoopError):
                install_foundation_documents(root, {"documents": leaking})
            self.assertFalse((root / FOUNDATION_ROOT).exists())


if __name__ == "__main__":
    unittest.main()
