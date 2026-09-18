"""Tracked files with sensitive-looking names may enter candidates only when explicitly allowed."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from storyloop.models import StoryLoopError
from storyloop.project_loop import _default_services
from storyloop.project_profile import detect_project, validate_project_settings
from storyloop.smoke_runner import _copy_candidate


def _repo(root: Path, allow: list[str]) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q",
                    "--allow-empty", "-m", "init"], cwd=root, check=True)
    (root / ".env.development").write_text("VITE_APP_TITLE=demo\n", encoding="utf-8")
    (root / "app.txt").write_text("app\n", encoding="utf-8")
    config = root / ".storyloop/config.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"profile": {"candidate_allow": allow}}), encoding="utf-8")


class CandidateAllowTests(unittest.TestCase):
    def test_profile_accepts_candidate_allow_and_rejects_ci_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(detect_project(root)["candidate_allow"], [])
            profile = validate_project_settings(root, {"candidate_allow": ["frontend/.env.production"]})
            self.assertEqual(profile["candidate_allow"], ["frontend/.env.production"])
            with self.assertRaises(StoryLoopError):
                validate_project_settings(root, {"candidate_allow": [".github/workflows/ci.yml"]})

    def test_sensitive_names_rejected_unless_allowed(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as target:
            root = Path(folder)
            _repo(root, [])
            with self.assertRaises(StoryLoopError):
                _copy_candidate(root, Path(target))
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as target:
            root = Path(folder)
            _repo(root, [".env.development"])
            paths = _copy_candidate(root, Path(target))
            self.assertIn(".env.development", paths)
            self.assertTrue((Path(target) / ".env.development").is_file())

    def test_smoke_agent_receives_suite_root(self):
        captured = {}

        def fake(project, role, payload):
            captured.update({"role": role, "payload": payload})
            return {}

        with patch("storyloop.project_agents.invoke_project_agent", fake):
            _default_services().generate_smoke(Path("/tmp/p"), {"change_id": "FEAT-1"})
        self.assertEqual(captured["role"], "smoke")
        self.assertEqual(captured["payload"]["suite_root"], "acceptance/smoke/FEAT-1")
        self.assertEqual(captured["payload"]["change"], {"change_id": "FEAT-1"})


if __name__ == "__main__":
    unittest.main()
