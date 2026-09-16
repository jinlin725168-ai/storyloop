"""v2 governance is mandatory unless a host explicitly selects compatibility."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from storyloop.loop_config_io import load_loop_config
from storyloop.models import StoryLoopError
from storyloop.spec_gate import validate_proposal
from storyloop.stage_coordinator import run_stage_graph
from tests.test_spec_gate import proposal
from tests.test_stage_config import loop_config
from tests.test_stage_coordinator import stage_green


class V2GovernanceRequiredTests(unittest.TestCase):
    def test_missing_governance_blocks_by_default_and_compatibility_is_explicit(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            with self.assertRaises(StoryLoopError):
                run_stage_graph(project, config, {},
                                lifecycle_runner=lambda *args, **kwargs: self.fail("must not run"))
            result = run_stage_graph(
                project, config, {}, require_governance=False,
                lifecycle_runner=lambda p, c, s, payload, **kwargs: stage_green(s),
            )
        self.assertEqual(result["status"], "PROJECT_GREEN")

    def test_parent_directory_link_cannot_escape_project_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project, outside = root / "project", root / "outside"
            project.mkdir()
            outside.mkdir()
            (outside / "proposal.md").write_text(proposal(), encoding="utf-8")
            (outside / "loop.json").write_text(json.dumps(loop_config()), encoding="utf-8")
            try:
                os.symlink(outside, project / "linked", target_is_directory=True)
            except OSError:
                self.skipTest("directory links are unavailable")
            with self.assertRaises(StoryLoopError):
                validate_proposal(project, "linked/proposal.md")
            with self.assertRaises(StoryLoopError):
                load_loop_config(project, "linked/loop.json")


if __name__ == "__main__":
    unittest.main()
