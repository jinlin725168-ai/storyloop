"""Build systems living one directory below the Git root are detected with prefixed paths."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from storyloop.project_profile import detect_project


class MonorepoDetectionTests(unittest.TestCase):
    def test_detects_backend_and_frontend_subprojects(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "backend").mkdir()
            (root / "backend/pom.xml").write_text("<project/>", encoding="utf-8")
            (root / "frontend").mkdir()
            (root / "frontend/package.json").write_text(
                json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8")
            (root / "frontend/pnpm-lock.yaml").write_text("lockfileVersion: '9'\n", encoding="utf-8")
            (root / "node_modules/skip").mkdir(parents=True)
            (root / "node_modules/skip/package.json").write_text("{}", encoding="utf-8")
            (root / ".hidden").mkdir()
            (root / ".hidden/pom.xml").write_text("<project/>", encoding="utf-8")

            profile = detect_project(root)

            self.assertEqual(profile["kinds"], ["maven", "node"])
            self.assertEqual(profile["integration_paths"],
                             ["backend/pom.xml", "frontend/package.json"])
            commands = profile["test_commands"]
            self.assertTrue(["mvn", "-f", "backend/pom.xml", "test"] in commands
                            or any("mvn" in item for item in profile["warnings"]))
            self.assertTrue(["pnpm", "--dir", "frontend", "test"] in commands
                            or any("pnpm" in item for item in profile["warnings"]))
            self.assertFalse(any("node_modules" in path or ".hidden" in path
                                 for path in profile["integration_paths"]))

    def test_root_project_is_not_duplicated_by_subdirectories(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "pom.xml").write_text("<project/>", encoding="utf-8")
            (root / "service").mkdir()
            (root / "service/pom.xml").write_text("<project/>", encoding="utf-8")

            profile = detect_project(root)

            self.assertEqual(profile["kinds"], ["maven"])
            self.assertEqual(profile["integration_paths"], ["pom.xml", "service/pom.xml"])


if __name__ == "__main__":
    unittest.main()
