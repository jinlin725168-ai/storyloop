"""Agent base runs cannot judge changes to their own engine."""

import unittest
from unittest import mock

from storyloop import base_execution, base_run_open, base_upgrade
from storyloop.approvals import approve_change
from storyloop.models import StoryLoopError
from storyloop.project_loop import _new_run_state
from tests import test_base_lifecycle as lifecycle


class BaseAgentSafetyTests(unittest.TestCase):
    setUp = lifecycle.BaseG1Tests.setUp

    def test_agent_self_hosting_is_rejected_before_branch_creation(self):
        proposed = base_upgrade.propose_base_change(
            self.root, ["storyloop/run_guards.py"], "调整引擎守卫")
        change = approve_change(self.root, proposed["change_id"])
        with mock.patch.object(base_run_open, "__file__", str(self.root / "storyloop/base_run_open.py")):
            with self.assertRaisesRegex(StoryLoopError, "self.host|T6|自举"):
                base_execution._open_state(self.root, change, "agent", _new_run_state)


if __name__ == "__main__":
    unittest.main()
