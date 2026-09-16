"""Historical audit attribution must not block a later adapter selection."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storyloop.run_artifacts import record_stage_attempt, verify_stage_attempt
from tests.test_replan import repeated_state
from tests.test_stage_config import loop_config


class RunArtifactAdapterSwitchTests(unittest.TestCase):
    def test_verification_uses_historical_adapter_from_committed_artifact(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            state = repeated_state(project, config)
            record_stage_attempt(project, config, state, "manual")
            verify_stage_attempt(project, config, state)


if __name__ == "__main__":
    unittest.main()
