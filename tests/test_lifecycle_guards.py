"""Fixed engine checks cannot be selected or skipped by a stage caller."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from storyloop.models import StoryLoopError
from storyloop.run_guards import guard_g1
from storyloop.run_transitions import transition
from storyloop.smoke import RED_EVIDENCE
from storyloop.smoke_runner import _judge_red, run_commands
from storyloop.storage import write_json
import sys


def red_record(project, status='RED'):
    path = project / '.storyloop/evidence/CHG-1' / RED_EVIDENCE
    write_json(path, {'change_id': 'CHG-1', 'status': status})
    return path


class PreparationGuardsTests(unittest.TestCase):
    def test_g1_missing_red_wrong_status_and_uncommitted_preparation_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = {'change_id': 'CHG-1', 'status': 'CONTRACT_COMMITTED', 'contract_commit': 'contract'}
            with self.assertRaises(StoryLoopError):
                guard_g1(root, state, {})
            red_record(root, 'GREEN')
            with self.assertRaises(StoryLoopError):
                guard_g1(root, state, {})
            red_record(root)
            del state['contract_commit']
            with self.assertRaises(StoryLoopError):
                guard_g1(root, state, {})

    def test_cached_red_keeps_the_historical_identity_and_status_only_semantics(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            red_record(root)  # historical evidence has no current head/suite/results bindings
            state = {'change_id': 'CHG-1', 'status': 'CONTRACT_COMMITTED', 'contract_commit': 'old'}
            guard_g1(root, state, {})
            transition(root, state, 'IMPLEMENTING', event='red_ready')
            self.assertEqual(state['status'], 'IMPLEMENTING')

    def test_guard_is_fixed_and_runs_before_implementation_effect(self):
        with tempfile.TemporaryDirectory() as folder:
            effect = Mock(return_value={})
            state = {'change_id': 'CHG-1', 'status': 'IMPLEMENTING', 'contract_commit': 'contract'}
            with self.assertRaises(StoryLoopError):
                transition(Path(folder), state, 'IMPLEMENTING', event='begin_attempt', effect=effect)
            effect.assert_not_called()
            self.assertEqual(state['status'], 'IMPLEMENTING')

    def test_fresh_zero_execution_environment_exit_and_timeout_cannot_prove_red(self):
        product = {'product': {'stories': [{'acceptance': [{'id': 'AC-1'}]}]}}
        with tempfile.TemporaryDirectory() as folder:
            for script in ("raise SystemExit(2)", "import time; time.sleep(2)",
                           "print('Ran 0 tests in 0.001s'); raise SystemExit(1)"):
                with self.subTest(script=script):
                    result = run_commands(Path(folder), [[sys.executable, '-c', script]], 1)
                    row = {**result['results'][0], 'criteria': ['AC-1']}
                    with self.assertRaises(StoryLoopError):
                        _judge_red([row], {'tests': [{'id': row['id']}]}, product)

    def test_real_preparation_cannot_enter_implementing_when_red_executor_is_skipped(self):
        from tests.test_failure_routing import approved
        from tests.test_project_loop import smoke_payload
        from storyloop.project_loop import run_change, LoopServices
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root)
            implement = Mock(return_value={'summary': 'unused', 'questions': []})
            services = LoopServices(lambda p, c: smoke_payload(change_id), implement, Mock())
            with patch('storyloop.project_preparation._ensure_red_evidence',
                       return_value={'evidence_sha256': 'forged'}):
                with self.assertRaises(StoryLoopError):
                    run_change(root, change_id, services)
            implement.assert_not_called()
