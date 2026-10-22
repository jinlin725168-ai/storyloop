"""The kernel validates edges before effects and owns the only status assignment."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from storyloop.models import StoryLoopError
from storyloop.run_transitions import TRANSITIONS, STATE_METADATA, transition, set_change_status
from storyloop.storage import load_json


class RunTransitionsTests(unittest.TestCase):
    def test_illegal_transition_keeps_state_and_never_runs_effect(self):
        state = {"change_id": "CHG-1", "status": "PREPARING"}
        before = deepcopy(state)
        effect = Mock(return_value={"merge_commit": "unused"})
        with self.assertRaises(StoryLoopError):
            transition(Path("."), state, "MERGED", event="merge", effect=effect)
        self.assertEqual(state, before)
        effect.assert_not_called()

    def test_every_registered_edge_accepts_its_pair_and_rejects_unknown_source(self):
        for event, pairs in TRANSITIONS.items():
            for source, target in pairs:
                with self.subTest(event=event, source=source, target=target):
                    state = {} if source is None else {"status": source}
                    transition(Path("."), state, target, event=event)
                    self.assertEqual(state["status"], target)
                    with self.assertRaises(StoryLoopError):
                        transition(Path("."), {"status": "UNKNOWN"}, target, event=event)

    def test_effect_sees_old_state_and_success_updates_have_no_persistence(self):
        state = {"status": "PREPARING"}
        def effect():
            self.assertEqual(state, {"status": "PREPARING"})
            return {"commit": "abc"}
        with tempfile.TemporaryDirectory() as folder:
            result = transition(Path(folder), state, "CONTRACT_READY", event="contract_ready",
                                updates={"attempt": 0}, effect=effect)
            self.assertEqual(list(Path(folder).iterdir()), [])
        self.assertEqual(result, {"commit": "abc"})
        self.assertEqual(state, {"status": "CONTRACT_READY", "commit": "abc", "attempt": 0})

    def test_effect_failure_does_not_advance_status(self):
        state = {"status": "PREPARING"}
        with self.assertRaisesRegex(RuntimeError, "crash"):
            transition(Path("."), state, "CONTRACT_READY", event="contract_ready",
                       effect=Mock(side_effect=RuntimeError("crash")))
        self.assertEqual(state, {"status": "PREPARING"})

    def test_status_cannot_hide_in_updates_or_effect_result(self):
        for argument in ({"updates": {"status": "PASSED"}},
                         {"effect": lambda: {"status": "PASSED"}}):
            state = {"status": "PREPARING"}
            with self.assertRaises(StoryLoopError):
                transition(Path("."), state, "CONTRACT_READY", event="contract_ready", **argument)
            self.assertEqual(state, {"status": "PREPARING"})

    def test_self_stop_is_legal_but_terminal_run_cannot_resume(self):
        state = {"status": "BUDGET_EXHAUSTED"}
        transition(Path("."), state, state["status"], event="stop")
        for status in ("PASSED", "SUPERSEDED"):
            with self.assertRaises(StoryLoopError):
                transition(Path("."), {"status": status}, "NEEDS_INPUT", event="stop")
            self.assertFalse(STATE_METADATA[status]["runnable"])

    def test_change_status_copies_the_aggregate_and_uses_only_named_events(self):
        change = {"change_id": "CHG-1", "status": "APPROVED", "superseded_by": "CHG-2"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            set_change_status(root, change, "SUPERSEDED")
            saved = load_json(root / ".storyloop/state/changes/CHG-1.json")
            self.assertEqual(saved, {**change, "status": "SUPERSEDED"})
            with self.assertRaises(StoryLoopError):
                set_change_status(root, change, "DRAFT")
        self.assertEqual(change["status"], "APPROVED")

    def test_completion_states_only_stop_on_actual_workspace_taint_path(self):
        for source in ('VERIFIED', 'MERGED', 'RECEIPTING', 'FINALIZING'):
            state = {'status': source}
            transition(Path('.'), state, 'WORKSPACE_TAINTED', event='stop')
            for target in ('EXISTING_GREEN', 'ORACLE_DEFECT', 'MANUAL_ACCEPTANCE_REQUIRED'):
                with self.subTest(source=source, target=target), self.assertRaises(StoryLoopError):
                    transition(Path('.'), {'status': source}, target, event='stop')
