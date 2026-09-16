"""Coordinator binding to governed OpenSpec and constitution inputs."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storyloop.constitution import generate_constitutions
from storyloop.models import StoryLoopError
from storyloop.stage_coordinator import run_stage_graph
from tests.test_spec_gate import proposal
from tests.test_stage_config import loop_config
from tests.test_stage_coordinator import stage_green


class GovernanceCoordinatorTests(unittest.TestCase):
    def test_changed_proposal_blocks_resume_with_stale_identity(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            spec = project / config["stages"][0]["specs"][0]
            spec.parent.mkdir(parents=True)
            spec.write_text(proposal(), encoding="utf-8")
            source = project / "governance/L1.md"
            source.parent.mkdir()
            source.write_text("# L1\n\nGoverned rules.\n", encoding="utf-8")
            generate_constitutions(project)
            run_stage_graph(
                project, config, {},
                lifecycle_runner=lambda p, c, s, payload, **kwargs: stage_green(s),
            )
            spec.write_text(proposal().replace("Safe feature", "Changed feature"),
                            encoding="utf-8")
            with self.assertRaisesRegex(StoryLoopError, "identity"):
                run_stage_graph(
                    project, config, {},
                    lifecycle_runner=lambda *args, **kwargs: self.fail("must not run"),
                )


if __name__ == "__main__":
    unittest.main()
