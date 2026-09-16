"""Replan must reject untrusted upstream fields before Agent invocation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storyloop.models import StoryLoopError
from storyloop.replan import replan_stage
from tests.test_replan import repeated_state
from tests.test_stage_config import loop_config


class ReplanSecurityTests(unittest.TestCase):
    def test_unknown_upstream_field_is_not_forwarded(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            state = repeated_state(project, config)
            handoff = {"stage": "prior", "result_digest": "a" * 64,
                       "changes": [], "judges": [], "secret": "never-forward"}
            with self.assertRaises(StoryLoopError):
                replan_stage(project, config, state, [handoff],
                             invoker=lambda *args, **kwargs: self.fail("must not invoke"))


if __name__ == "__main__":
    unittest.main()
