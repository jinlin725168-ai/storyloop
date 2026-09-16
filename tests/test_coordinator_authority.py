"""Coordinator summaries cannot grant themselves Stage success."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storyloop.coordinator_authority import stage_record
from storyloop.coordinator_state import load_or_create, save_coordinator
from storyloop.models import StoryLoopError
from storyloop.stage_state import new_stage_state, save_stage_state
from tests.test_stage_config import loop_config
from tests.test_stage_coordinator import stage_green


class CoordinatorAuthorityTests(unittest.TestCase):
    def test_forged_green_summary_cannot_override_ready_stage_state(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            config = loop_config()
            payload = {"input": {}, "upstream": []}
            save_stage_state(
                project, new_stage_state(project, config, "foundation", payload),
            )
            coordinator = load_or_create(
                project, config, {}, None,
                require_governance=False, verify_stage_states=False,
            )
            forged = stage_record("foundation", stage_green("foundation"))
            coordinator = {**coordinator, "status": "STAGE_GREEN",
                           "stages": {"foundation": forged}}
            save_coordinator(project, coordinator)

            with self.assertRaises(StoryLoopError):
                load_or_create(
                    project, config, {}, None,
                    require_governance=False, verify_stage_states=True,
                )


if __name__ == "__main__":
    unittest.main()
