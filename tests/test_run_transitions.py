"""The kernel validates edges before effects and owns the only status assignment."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

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
                    if event == "reset" and source == "WORKSPACE_TAINTED": state["interrupted_status"] = target
                    if event == "cost_restore": state["cost_resume_status"] = target
                    with patch("storyloop.run_transitions.guard_g1"), \
                            patch("storyloop.run_transitions.guard_g2"), \
                            patch("storyloop.run_transitions.guard_g3"), \
                            patch("storyloop.run_transitions.guard_cost_restore"), \
                            patch("storyloop.run_transitions.recover_completed_merge", return_value="merge"):
                        transition(Path("."), state, target, event=event, change={})
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

    def test_aggregate_copy_is_returned_for_existing_supersede_preview_side_effect(self):
        original = {'change_id': 'CHG-1', 'status': 'APPROVED'}
        with tempfile.TemporaryDirectory() as folder:
            result = set_change_status(Path(folder), original, 'SUPERSEDED')
        self.assertEqual(result, {**original, 'status': 'SUPERSEDED'})
        self.assertEqual(original['status'], 'APPROVED')

    def test_snapshot_recovery_cannot_choose_a_different_target(self):
        for source, event, extra in (
            ('WORKSPACE_TAINTED', 'reset', {'interrupted_status': 'CONTRACT_COMMITTED'}),
            ('COST_LIMIT_REACHED', 'cost_restore', {'cost_resume_status': 'CONTRACT_READY'}),
        ):
            state = {'status': source, **extra}
            with self.subTest(event=event), self.assertRaises(StoryLoopError):
                transition(Path('.'), state, 'PASSED', event=event)
            self.assertEqual(state['status'], source)

    def test_reset_keeps_historical_interrupted_values_without_hashing_them(self):
        for target in ('OLD_PHASE', ['historical'], {'legacy': 'phase'}):
            state = {'status': 'WORKSPACE_TAINTED', 'interrupted_status': target}
            transition(Path('.'), state, target, event='reset')
            self.assertEqual(state['status'], target)

    def test_absent_cost_snapshot_is_not_an_authorized_none_target(self):
        state = {'status': 'COST_LIMIT_REACHED'}
        with patch('storyloop.run_transitions.guard_cost_restore'):
            with self.assertRaises(StoryLoopError):
                transition(Path('.'), state, None, event='cost_restore')
        self.assertEqual(state, {'status': 'COST_LIMIT_REACHED'})

    def test_committed_contract_can_pause_for_planning_without_bypassing_g1(self):
        state = {'status': 'CONTRACT_COMMITTED', 'change_id': 'CHG-1', 'contract_commit': 'contract'}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for target in ('DESIGN_APPROVAL_REQUIRED', 'DESIGN_REVISION_REQUIRED', 'NO_PROGRESS'):
                with self.subTest(target=target), self.assertRaises(StoryLoopError):
                    transition(root, dict(state), target, event='stop')
            transition(root, state, 'NEEDS_INPUT', event='stop')
            self.assertEqual(state['status'], 'NEEDS_INPUT')
            with self.assertRaisesRegex(StoryLoopError, 'RED evidence is missing'):
                transition(root, state, 'IMPLEMENTING', event='begin_attempt')
            self.assertEqual(state['status'], 'NEEDS_INPUT')
