"""Fixed engine checks cannot be selected or skipped by a stage caller."""
from pathlib import Path
from dataclasses import replace
from types import MappingProxyType
import tempfile
import unittest
from unittest.mock import Mock, patch
from storyloop import change_types
from storyloop.change_types import CHANGE_TYPES
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
    def test_g1_rejects_a_type_without_a_supported_red_floor(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            red_record(root)
            state = {'change_id': 'CHG-1', 'status': 'CONTRACT_COMMITTED', 'contract_commit': 'contract'}
            policy = replace(CHANGE_TYPES['feature'], criteria_floor=())
            with patch.object(change_types, 'CHANGE_TYPES',
                              MappingProxyType({**CHANGE_TYPES, 'feature': policy})):
                with self.assertRaisesRegex(StoryLoopError, 'RED floor'):
                    guard_g1(root, state, {})

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


class VerificationGuardsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.lifecycle_guard_cases import verified_candidate
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.candidate = verified_candidate(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_g2_rejects_failed_missing_zero_count_below_floor_review_and_boundary(self):
        from copy import deepcopy
        from storyloop.run_guards import guard_g2
        for violation in ('failed', 'missing', 'zero', 'floor', 'fail_review', 'question', 'boundary'):
            case = deepcopy(self.candidate)
            state, context = case['state'], case['context']
            if violation == 'failed': context['layers']['unit']['status'] = 'FAILED'
            if violation == 'missing': context['layers'].pop('unit')
            if violation == 'zero': context['layers']['unit']['results'][0]['counts']['executed'] = 0
            if violation == 'floor': state['oracle']['baseline']['unit'] = [2]
            if violation == 'fail_review': context['review']['verdict'] = 'fail'
            if violation == 'question': context['review']['verdict'] = 'needs_input'
            if violation == 'boundary': context['boundary']['violations'] = [{'path': 'core.py'}]
            effect = Mock(return_value={})
            before = deepcopy(state)
            with self.subTest(violation=violation), self.assertRaises(StoryLoopError):
                transition(self.root, state, 'VERIFIED', event='verified',
                           change=case['change'], context=context, effect=effect)
            self.assertEqual(state, before)
            effect.assert_not_called()

    def test_real_completion_cannot_skip_a_configured_unit_layer(self):
        from copy import deepcopy
        from storyloop.project_loop_finish import complete_change
        case = deepcopy(self.candidate)
        case['context']['layers']['unit'] = {'status': 'SKIPPED', 'results': []}
        with patch('storyloop.project_loop_finish.commit_paths') as commit:
            with self.assertRaises(StoryLoopError):
                complete_change(self.root, case['change'], case['state'], **case['context'])
            commit.assert_not_called()

    def test_g2_accepts_real_evidence_and_preserves_uncounted_legacy_layers(self):
        from copy import deepcopy
        from storyloop.run_guards import guard_g2
        case = deepcopy(self.candidate)
        guard_g2(self.root, case['change'], case['state'], case['context'])
        case['context']['layers']['unit']['results'][0]['counts']['executed'] = None
        guard_g2(self.root, case['change'], case['state'], case['context'])

    def test_g2_reads_the_type_writable_scope(self):
        from copy import deepcopy
        from storyloop.run_guards import guard_g2
        case = deepcopy(self.candidate)
        self.assertTrue(any(item['category'] == 'FEATURE' for item in case['context']['boundary']['allowed']))
        policy = replace(CHANGE_TYPES['feature'], writable_scope=('test_roots',))
        with patch.object(change_types, 'CHANGE_TYPES',
                          MappingProxyType({**CHANGE_TYPES, 'feature': policy})):
            with self.assertRaisesRegex(StoryLoopError, 'boundary|Workspace changed'):
                guard_g2(self.root, case['change'], case['state'], case['context'])

    def test_g2_rejects_tampered_acceptance_evidence_before_effect(self):
        from copy import deepcopy
        from storyloop.smoke import RESULT_EVIDENCE
        case = deepcopy(self.candidate)
        path = self.root / '.storyloop/evidence' / case['state']['change_id'] / RESULT_EVIDENCE
        before = path.read_bytes()
        try:
            path.write_bytes(before + b' ')
            effect = Mock(return_value={})
            with self.assertRaises(StoryLoopError):
                transition(self.root, case['state'], 'VERIFIED', event='verified',
                           change=case['change'], context=case['context'], effect=effect)
            effect.assert_not_called()
        finally:
            path.write_bytes(before)

    def test_g2_manual_acceptance_is_bound_to_current_candidate(self):
        from tests.lifecycle_guard_cases import verified_candidate
        from storyloop.manual_acceptance import record_manual_acceptance
        from storyloop.run_guards import guard_g2
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            case = verified_candidate(root, manual=True)
            with self.assertRaises(StoryLoopError):
                guard_g2(root, case['change'], case['state'], case['context'])
            record_manual_acceptance(root, case['state']['change_id'], ['AC-2'], 'checked')
            guard_g2(root, case['change'], case['state'], case['context'])


class MergeGuardsTests(unittest.TestCase):
    def test_g3_rejects_dirty_wrong_branch_report_and_source_before_effect(self):
        state = {'status': 'VERIFIED', 'feature_branch': 'feature', 'source_branch': 'main',
                 'report_commit': 'report', 'source_head': 'source'}
        for violation in ('dirty', 'branch', 'report', 'source'):
            def git(project, *args):
                if args[0] == 'status': return ' M file' if violation == 'dirty' else ''
                branch = args[1]
                expected = 'report' if branch == 'feature' else 'source'
                return 'changed' if violation == expected else expected
            effect = Mock(return_value={'merge_commit': 'merge'})
            with patch('storyloop.run_guards._git', side_effect=git), \
                    patch('storyloop.run_guards.current_branch',
                          return_value='wrong' if violation == 'branch' else 'feature'), \
                    patch('storyloop.run_guards.verify_verified_artifacts'):
                with self.subTest(violation=violation), self.assertRaises(StoryLoopError):
                    transition(Path('.'), dict(state), 'MERGED', event='merge', effect=effect)
                effect.assert_not_called()

    def test_g3_checks_artifacts_before_successful_delivery(self):
        state = {'status': 'VERIFIED', 'feature_branch': 'feature', 'source_branch': 'main',
                 'report_commit': 'report', 'source_head': 'source'}
        order = []
        def git(project, *args):
            return '' if args[0] == 'status' else 'report' if args[1] == 'feature' else 'source'
        with patch('storyloop.run_guards._git', side_effect=git), \
                patch('storyloop.run_guards.current_branch', return_value='feature'), \
                patch('storyloop.run_guards.verify_verified_artifacts', side_effect=lambda *a: order.append('guard')):
            def effect():
                order.append('merge')
                return {'merge_commit': 'merge'}
            transition(Path('.'), state, 'MERGED', event='merge', effect=effect)
        self.assertEqual(order, ['guard', 'merge'])
        self.assertEqual(state['status'], 'MERGED')
