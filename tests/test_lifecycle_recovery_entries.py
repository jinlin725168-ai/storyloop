"""Exercise each existing recovery fixture through its named, unavoidable transition entry."""
import importlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.run_transitions import transition as real_transition


class EntryReached(RuntimeError):
    pass


CASES = [
    ('acceptance_repair', 'test_smoke_repair', 'SmokeRepairTests',
     'test_repair_replaces_suite_reproves_red_at_contract_commit_and_run_passes', 'repair_red'),
    ('run_recovery', 'test_prepare_reset', 'PrepareResetTests',
     'test_red_taint_reset_cannot_skip_red_or_baseline', 'reset'),
    ('project_loop_failures', 'test_failure_routing', 'ReviewRoutingTests',
     'test_an_overrule_rewrites_the_finding_target_and_signature', 'overrule'),
    ('consistency', 'test_consistency', 'ConsistencyGateTests',
     'test_overrule_preserves_remaining_oracle_then_design_blockers', 'consistency_overrule'),
    ('cost_resume', 'test_cost_limits', 'CostResumeTests',
     'test_review_stop_keeps_candidate_and_same_attempt', 'cost_restore'),
    ('design_approval', 'test_design_approval', 'DesignApprovalTests',
     'test_failed_characterization_change_option_restarts_gate', 'design_reapprove'),
    ('design_approval', 'test_design_approval', 'DesignApprovalTests',
     'test_replan_only_planning_and_retains_budget_attempt', 'design_replan'),
    ('routing_upgrade', 'test_routing_upgrade', 'L3IntegrationTests',
     'test_real_implement_risk_escalates_once_without_failure_pollution', 'l3_replan'),
    ('supersede', 'test_supersede', 'SupersedeTests',
     'test_contract_stop_reopens_as_linked_draft', 'supersede'),
    ('supersede', 'test_supersede', 'SupersedeTests',
     'test_retry_rebuilds_preview_after_interrupted_child_creation', 'supersede_result'),
]


class RecoveryEntryTests(unittest.TestCase):
    def test_existing_recovery_fixtures_cannot_bypass_named_entry(self):
        for module, tests, classname, method, event in CASES:
            with self.subTest(event=event):
                case = getattr(importlib.import_module('tests.'+tests), classname)(method)
                with self._entry(module, event), self.assertRaisesRegex(EntryReached, event):
                    try:
                        case.setUp()
                        getattr(case, method)()
                    finally:
                        case.tearDown()
                        case.doCleanups()

    def test_completed_merge_recovery_uses_its_own_checked_event(self):
        from tests.lifecycle_cases import approved_case, observed_services
        from storyloop.project_git import finish_feature
        from storyloop.project_loop import run_change
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            identifier = approved_case(root, 'verification')
            services = observed_services(identifier, 'verification', [])
            def crash(*args):
                finish_feature(*args)
                raise RuntimeError('after actual merge')
            with patch('storyloop.project_loop_finish.finish_feature', side_effect=crash):
                with self.assertRaisesRegex(RuntimeError, 'after actual merge'):
                    run_change(root, identifier, services)
            with self._entry('project_loop', 'recover_merge'), self.assertRaisesRegex(EntryReached, 'recover_merge'):
                run_change(root, identifier, services)

    def _entry(self, module, expected):
        def capture(*args, **kwargs):
            if kwargs.get('event') == expected:
                raise EntryReached(expected)
            return real_transition(*args, **kwargs)
        return patch('storyloop.'+module+'.transition', side_effect=capture, create=True)

    def test_supersede_keeps_historical_aggregate_input_status(self):
        from tests.test_supersede import contract_stop, revised
        from storyloop.changes import load_change
        from storyloop.supersede import supersede_change
        from storyloop.storage import write_json
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            identifier = contract_stop(root)
            old = load_change(root, identifier)
            old['status'] = 'RUNNING'
            write_json(root / '.storyloop/state/changes' / (identifier+'.json'), old)
            result = supersede_change(root, identifier, 'revise', revised)
            self.assertEqual(result['status'], 'DRAFT')
            self.assertEqual(load_change(root, identifier)['status'], 'SUPERSEDED')
