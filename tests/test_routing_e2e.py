"""Real isolated gates across routed tiers; mocks observe roles, never replace gates."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

from storyloop.design_approval import approve_design, replan
from storyloop.models import StoryLoopError
from storyloop.project_loop import LoopServices, run_change, load_run_state
from storyloop.approvals import approve_change
from storyloop.base_execution import record_base_review
from storyloop.baseline import load_baseline
from storyloop.manual_acceptance import record_manual_acceptance
from storyloop.onboarding_cli import bootstrap_with_project_checks
from storyloop.smoke import RED_EVIDENCE
from storyloop.storage import load_json
from tests.routing_support import routed_case
from tests.routing_support import legacy_create_change
from tests.support import product
from tests.test_baseline_behavior import git, repository
from tests.test_project_loop import smoke_payload


class RoutedEndToEndTests(unittest.TestCase):
    def case(self, tier):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        change, services = routed_case(root, tier)
        observed = replace(services, implement=Mock(wraps=services.implement),
                           design=Mock(wraps=services.design),
                           characterize=Mock(wraps=services.characterize),
                           review=Mock(wraps=services.review))
        return root, change['change_id'], observed

    def test_light_standard_heavy_real_gates_and_call_counts(self):
        for tier, design_calls, lock_calls in [('light', 0, 0), ('standard', 1, 0), ('heavy', 1, 1)]:
            with self.subTest(tier=tier):
                root, identifier, services = self.case(tier)
                result = run_change(root, identifier, services)
                if tier == 'heavy':
                    before = load_run_state(root, identifier)
                    self.assertEqual(before['status'], 'DESIGN_APPROVAL_REQUIRED')
                    self.assertEqual(before['attempt'], 0)
                    services.implement.assert_not_called()
                    services.characterize.assert_not_called()
                    services.review.assert_not_called()
                    approve_design(root, identifier, before['planning']['plan']['id'])
                    result = run_change(root, identifier, services)
                self.assertEqual(result['status'], 'PASSED', result)
                self.assertEqual(services.implement.call_count, 1)
                self.assertEqual(services.design.call_count, design_calls)
                self.assertEqual(services.characterize.call_count, lock_calls)
                state = load_run_state(root, identifier)
                self.assertEqual(state['routing']['effective_tier'], tier)
                self.assertEqual(state['attempt'], 1)
                self.assertTrue(state['integration_commit'])
                self.assertTrue(state['merge_commit'])
                modes = [call.args[1].get('mode') for call in services.review.call_args_list]
                self.assertEqual(modes.count('consistency'), int(tier == 'heavy'))
                self.assertGreater(len(modes), int(tier == 'heavy'))
                self.assertEqual(state['characterization']['status'],
                                 'GREEN' if tier == 'heavy' else 'DEFERRED')
                from storyloop.storage import load_json
                layers = load_json(Path(state['report']))['layers']
                for layer in ('unit', 'smoke', 'acceptance'):
                    self.assertGreater(layers[layer]['results'][0]['counts']['executed'], 0)
                self.assertEqual(layers['checks']['status'], 'PASSED')

    def test_invalid_approval_and_replan_cannot_spend_attempt(self):
        root, identifier, services = self.case('heavy')
        run_change(root, identifier, services)
        with self.assertRaises(StoryLoopError):
            approve_design(root, identifier, 'stale-plan')
        self.assertEqual(run_change(root, identifier, services)['status'], 'DESIGN_APPROVAL_REQUIRED')
        replan(root, identifier, 'Revise proposed implementation before consent')
        second = run_change(root, identifier, services)
        self.assertEqual(second['status'], 'DESIGN_APPROVAL_REQUIRED')
        self.assertEqual(services.design.call_count, 2)
        services.implement.assert_not_called()
        self.assertEqual(load_run_state(root, identifier)['attempt'], 0)
        approve_design(root, identifier, second['plan_id'])
        self.assertEqual(run_change(root, identifier, services)['status'], 'PASSED')

    def test_empty_lock_response_is_rejected_before_implementation(self):
        root, identifier, services = self.case('heavy')
        services = replace(services, characterize=Mock(return_value={
            'summary': 'No locks', 'touched': [], 'tests': [], 'questions': []}))
        pause = run_change(root, identifier, services)
        approve_design(root, identifier, pause['plan_id'])
        with self.assertRaisesRegex(StoryLoopError, 'coverage|artifact'):
            run_change(root, identifier, services)
        services.implement.assert_not_called()
        self.assertEqual(load_run_state(root, identifier)['attempt'], 0)

    def test_old_design_adapter_cannot_fall_back_for_new_standard(self):
        root, identifier, services = self.case('standard')
        services = replace(services, design=Mock(return_value={
            'summary': 'old adapter', 'touched': [], 'constraints': [], 'questions': []}))
        with self.assertRaises(StoryLoopError):
            run_change(root, identifier, services)
        self.assertEqual(services.design.call_count, 2)
        services.implement.assert_not_called()
        self.assertEqual(load_run_state(root, identifier)['attempt'], 0)

    def test_split_continuation_still_requires_design_consent(self):
        from storyloop.design_approval import approve_continue_heavy
        root, identifier, services = self.case('heavy')
        original_design = services.design
        def expanded(project, context):
            value = original_design(project, context)
            value['options'][0]['creates'] += [f'app/extra{i}.py' for i in range(15)]
            return value
        services = replace(services, design=Mock(side_effect=expanded))
        pause = run_change(root, identifier, services)
        self.assertEqual(pause['status'], 'SPLIT_RECOMMENDED')
        state = load_run_state(root, identifier)
        assessment = state['planning']['assessment']['id']
        with self.assertRaises(StoryLoopError):
            approve_continue_heavy(root, identifier, 'stale', 'Single delivery')
        with self.assertRaises(StoryLoopError):
            approve_continue_heavy(root, identifier, assessment, '')
        approve_continue_heavy(root, identifier, assessment, 'Single atomic delivery')
        pause = run_change(root, identifier, services)
        self.assertEqual(pause['status'], 'DESIGN_APPROVAL_REQUIRED')
        services.implement.assert_not_called()
        services.characterize.assert_not_called()
        self.assertEqual(load_run_state(root, identifier)['attempt'], 0)
        approve_design(root, identifier, pause['plan_id'])
        self.assertEqual(run_change(root, identifier, services)['status'], 'PASSED')

    def test_l3_risk_preserves_attempt_and_requires_reapproval_after_locks(self):
        root, identifier, services = self.case('standard')
        original = services.implement
        def implement(project, context):
            result = original(project, context)
            result['routing_risks'] = [{'risk': 'migration', 'statement': 'Storage format changes',
                                        'location': 'app/service.py:1'}]
            return result
        services = replace(services, implement=Mock(side_effect=implement), characterize=None)
        pause = run_change(root, identifier, services)
        self.assertEqual(pause['status'], 'DESIGN_APPROVAL_REQUIRED')
        before = load_run_state(root, identifier)
        self.assertEqual(before['attempt'], 1)
        self.assertEqual(before['planning']['consumed_attempt'], 1)
        self.assertEqual(before['routing']['effective_tier'], 'heavy')
        approve_design(root, identifier, pause['plan_id'])
        from tests.routing_support import routed_characterize
        services = replace(services, characterize=Mock(wraps=routed_characterize))
        pause = run_change(root, identifier, services)
        self.assertEqual(pause['status'], 'DESIGN_APPROVAL_REQUIRED')
        self.assertEqual(services.implement.call_count, 1)
        approve_design(root, identifier, pause['plan_id'])
        self.assertEqual(run_change(root, identifier, services)['status'], 'PASSED')
        self.assertEqual(services.characterize.call_count, 1)
        self.assertEqual(services.implement.call_count, 2)

    def test_explicit_budget_increase_resumes_without_refunding_failures(self):
        from storyloop.storage import load_json, write_json
        root, identifier, services = self.case('light')
        config_path = root / '.storyloop/config.json'
        config = load_json(config_path)
        config['max_attempts'] = 1
        write_json(config_path, config)
        failed = replace(services, implement=Mock(return_value={'summary': 'Incomplete', 'questions': []}))
        self.assertEqual(run_change(root, identifier, failed)['status'], 'BUDGET_EXHAUSTED')
        self.assertEqual(failed.implement.call_count, 1)
        config['max_attempts'] = 2
        write_json(config_path, config)
        self.assertEqual(run_change(root, identifier, services)['status'], 'PASSED')
        state = load_run_state(root, identifier)
        self.assertEqual(state['attempt'], 2)
        self.assertEqual(state['routing']['budget_source'], 'explicit')
        self.assertEqual(state['routing']['limit'], 2)

    def test_manual_acceptance_is_separate_from_heavy_design_approval(self):
        from copy import deepcopy
        from storyloop.changes import load_change, revise_change
        from storyloop.approvals import approve_change
        from storyloop.manual_acceptance import record_manual_acceptance
        from tests.test_routing_planning import answer
        root, identifier, services = self.case('heavy')
        value = load_change(root, identifier)['product']
        criterion = deepcopy(value['stories'][0]['acceptance'][0])
        criterion.update(id='AC-2', kind='manual')
        value['stories'][0]['acceptance'].append(criterion)
        assessment = deepcopy(value['assessment']['criteria'][0])
        assessment.update(criterion_id='AC-2', feasibility='manual')
        assessment['method']['steps'] = ['Inspect delivered order behavior']
        value['assessment']['criteria'].append(assessment)
        revise_change(root, identifier, value)
        approve_change(root, identifier)
        services = replace(services, design=Mock(side_effect=lambda p, c: answer(value)))
        with self.assertRaises(StoryLoopError):
            record_manual_acceptance(root, identifier, ['AC-2'], 'Too early')
        pause = run_change(root, identifier, services)
        approve_design(root, identifier, pause['plan_id'])
        self.assertEqual(run_change(root, identifier, services)['status'], 'MANUAL_ACCEPTANCE_REQUIRED')
        with self.assertRaises(StoryLoopError):
            record_manual_acceptance(root, identifier, ['AC-1'], 'Wrong automated criterion')
        record_manual_acceptance(root, identifier, ['AC-2'], 'Inspected delivered behavior')
        self.assertEqual(run_change(root, identifier, services)['status'], 'PASSED')
        self.assertEqual(services.implement.call_count, 1)

    def test_deferred_locks_run_only_after_first_existing_path_edit(self):
        root, identifier, services = self.case('standard')
        events = []
        original_implement, original_lock = services.implement, services.characterize
        def implement(project, context):
            events.append('implement')
            result = original_implement(project, context)
            (project / 'app/service.py').write_text('def serve():\n    return "ok"  # refactored\n')
            return result
        def characterize(project, context):
            events.append('characterize')
            self.assertEqual(context['extend'], ['app/service.py'])
            self.assertNotIn('refactored', (project / 'app/service.py').read_text())
            return original_lock(project, context)
        services = replace(services, implement=implement, characterize=characterize)
        self.assertEqual(run_change(root, identifier, services)['status'], 'PASSED')
        self.assertEqual(events, ['implement', 'characterize', 'implement'])
        self.assertEqual(load_run_state(root, identifier)['attempt'], 2)

    def test_deferred_missing_service_rejects_before_restoring_candidate(self):
        root, identifier, services = self.case('standard')
        original = services.implement
        def implement(project, context):
            result = original(project, context)
            (project / 'app/service.py').write_text('def serve():\n    return "ok"  # candidate\n')
            return result
        services = replace(services, implement=implement, characterize=None)
        with self.assertRaisesRegex(StoryLoopError, 'characterization'):
            run_change(root, identifier, services)
        self.assertIn('candidate', (root / 'app/service.py').read_text())

    def test_refactor_retains_preimplementation_lock_gate(self):
        from storyloop.changes import load_change, revise_change
        from storyloop.approvals import approve_change
        from tests.test_routing_planning import answer
        root, identifier, services = self.case('standard')
        value = load_change(root, identifier)['product']
        value.update(nature='refactor', refactor={'subtype': 'structure',
            'scope': ['app/service.py'], 'invariants': ['serve stays ok'],
            'structural_goals': ['Separate order implementation']})
        revise_change(root, identifier, value)
        approve_change(root, identifier, tier='standard')
        services = replace(services, design=Mock(side_effect=lambda p, c: answer(value)))
        with self.assertRaisesRegex(StoryLoopError, 'characterization gate'):
            run_change(root, identifier, replace(services, characterize=None))
        services.implement.assert_not_called()
        events = []
        implement, characterize = services.implement, services.characterize
        services = replace(services,
            implement=lambda p, c: events.append('implement') or implement(p, c),
            characterize=lambda p, c: events.append('characterize') or characterize(p, c))
        self.assertEqual(run_change(root, identifier, services)['status'], 'PASSED')
        self.assertEqual(events, ['characterize', 'implement'])

    def test_oracle_repair_reproves_red_and_requires_new_approval(self):
        from storyloop.acceptance_repair import repair_smoke
        from tests.test_consistency import coherent, finding
        from tests.routing_support import routed_acceptance
        root, identifier, services = self.case('heavy')
        pause = run_change(root, identifier, services)
        approve_design(root, identifier, pause['plan_id'])
        blocked = {**coherent(), 'verdict': 'blocked', 'findings': [
            finding('oracle', f'acceptance/changes/{identifier}/flow.py:1')]}
        bad = replace(services, review=Mock(return_value=blocked))
        self.assertEqual(run_change(root, identifier, bad)['status'], 'ORACLE_DEFECT')
        with self.assertRaisesRegex(StoryLoopError, 'repair'):
            replan(root, identifier, 'Cannot evade defective oracle')
        services.implement.assert_not_called()
        def repaired(project, context):
            payload = routed_acceptance(identifier)
            payload['files'][0]['content'] += '\n# corrected oracle obligation\n'
            return payload
        repair_smoke(root, identifier, 'Correct oracle obligation',
                     replace(services, generate_smoke=repaired))
        state = load_run_state(root, identifier)
        self.assertEqual(state['attempt'], 0)
        self.assertTrue(state['red_evidence_sha256'])
        self.assertNotIn('plan', state['planning'])
        pause = run_change(root, identifier, services)
        self.assertEqual(pause['status'], 'DESIGN_APPROVAL_REQUIRED')
        approve_design(root, identifier, pause['plan_id'])
        self.assertEqual(run_change(root, identifier, services)['status'], 'PASSED')

    def test_base_upgrade_invalidates_plan_then_replan_completes(self):
        from storyloop.base_upgrade import upgrade_baseline
        from tests.test_baseline_behavior import git
        root, identifier, services = self.case('heavy')
        pause = run_change(root, identifier, services)
        approve_design(root, identifier, pause['plan_id'])
        def touch_base(project, context):
            (project / 'core.py').write_text('STABLE = False\n')
            return {'summary': 'Requires foundation change', 'questions': []}
        pause = run_change(root, identifier, replace(services, implement=touch_base))
        self.assertEqual(pause['status'], 'BASE_CHANGE_REQUIRED')
        state = load_run_state(root, identifier)
        # Exercise the historical unlinked receipt path, which E3 leaves unchanged.
        from storyloop.project_loop_support import save_run
        state.pop('blocked_by', None)
        save_run(root, state)
        proposal = Path(pause['base_proposal']).stem
        self.assertEqual(upgrade_baseline(root, proposal)['status'], 'APPROVAL_REQUIRED')
        git(root, 'checkout', '--', 'core.py')
        git(root, 'checkout', '-q', state['source_branch'])
        (root / 'core.py').write_text('STABLE = False\n')
        git(root, 'commit', '-qam', 'base: update core')
        from tests.test_base_upgrade import begin_legacy_receipt
        begin_legacy_receipt(root, root / '.storyloop/base-proposals' / f'{proposal}.json')
        self.assertEqual(upgrade_baseline(root, proposal)['status'], 'BASELINE_UPGRADED')
        git(root, 'checkout', '-q', state['feature_branch'])
        git(root, 'merge', '-q', '--no-edit', state['source_branch'])
        self.assertEqual(run_change(root, identifier, services)['status'], 'DESIGN_REVISION_REQUIRED')
        self.assertEqual(load_run_state(root, identifier)['attempt'], 1)
        replan(root, identifier, 'Adopt verified BASE update')
        pause = run_change(root, identifier, services)
        self.assertEqual(pause['status'], 'DESIGN_APPROVAL_REQUIRED')
        approve_design(root, identifier, pause['plan_id'])
        self.assertEqual(run_change(root, identifier, services)['status'], 'PASSED')
        self.assertEqual(load_run_state(root, identifier)['attempt'], 2)

    def test_historical_routed_skip_cannot_disable_dynamic_lock(self):
        from storyloop.project_loop_support import save_run
        from tests.test_agent_unavailable import unavailable
        root, identifier, services = self.case('standard')
        stopped = replace(services, implement=Mock(side_effect=unavailable()))
        with self.assertRaises(StoryLoopError):
            run_change(root, identifier, stopped)
        state = load_run_state(root, identifier)
        state['characterization'] = {'status': 'SKIPPED', 'reason': 'no characterize service',
                                     'touched': [], 'tests': []}
        save_run(root, state)
        original = services.implement
        def implement(project, context):
            result = original(project, context)
            (project / 'app/service.py').write_text('def serve():\n    return "ok"  # updated\n')
            return result
        services = replace(services, implement=implement)
        self.assertEqual(run_change(root, identifier, services)['status'], 'PASSED')
        self.assertEqual(services.characterize.call_count, 1)
        self.assertEqual(load_run_state(root, identifier)['attempt'], 2)

class E3EndToEndTests(unittest.TestCase):
    def test_human_base_then_original_feature_both_pass_with_new_red(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            smoke = root / "acceptance/smoke"
            smoke.mkdir(parents=True)
            (smoke / "check.py").write_text("print('smoke green')\n", encoding="utf-8")
            (smoke / "manifest.json").write_text(
                '{"version":1,"tests":[{"id":"check","description":"check",'
                '"command":["{python}","acceptance/smoke/check.py"],"timeout_seconds":30}]}',
                encoding="utf-8")
            git(root, "add", ".")
            git(root, "commit", "-q", "-m", "smoke")
            bootstrap_with_project_checks(root, {
                "unit_commands": [[sys.executable, "-c", "print('unit green')"]],
                "integration_commands": [], "check_commands": [], "base_build_commands": [],
            })
            feature = legacy_create_change(root, "orders", product(),
                                           now=lambda: datetime(2026, 10, 28, tzinfo=timezone.utc))
            feature_id = feature["change_id"]
            approve_change(root, feature_id)

            def collide(project, context):
                (project / "pom.xml").write_text("<project><dependency>needed</dependency></project>\n",
                                                  encoding="utf-8")
                return {"summary": "dependency required", "questions": []}

            review = lambda project, context: {"verdict": "pass", "summary": "verified",
                                               "findings": [], "questions": []}
            first = run_change(root, feature_id, LoopServices(
                generate_smoke=lambda project, change: smoke_payload(feature_id),
                implement=collide, review=review))
            self.assertEqual(first["status"], "BASE_CHANGE_REQUIRED")
            base_id = first["blocked_by"]
            old_red = load_json(root / ".storyloop/evidence" / feature_id / RED_EVIDENCE)
            feature_branch = git(root, "branch", "--show-current")
            git(root, "switch", load_baseline(root)["source_branch"])
            approve_change(root, base_id)
            self.assertEqual(run_change(root, base_id, implementer="human")["status"],
                             "HUMAN_IMPLEMENTATION_REQUIRED")
            (root / "pom.xml").write_text("<project><dependency>accepted</dependency></project>\n",
                                            encoding="utf-8")
            git(root, "add", "pom.xml")
            git(root, "commit", "-q", "-m", "base dependency")
            self.assertEqual(run_change(root, base_id)["status"], "BASE_REVIEW_REQUIRED")
            git(root, "config", "user.name", "Independent Reviewer")
            record_base_review(root, base_id, "pass", "已核对依赖")
            self.assertEqual(run_change(root, base_id)["status"], "MANUAL_ACCEPTANCE_REQUIRED")
            record_manual_acceptance(root, base_id, ["BASE-ACCEPT"], "依赖满足需求")
            self.assertEqual(run_change(root, base_id)["status"], "FINALIZING")
            self.assertEqual(run_change(root, base_id)["status"], "PASSED")
            git(root, "switch", feature_branch)

            def implement(project, context):
                target = project / "modules/orders/service.py"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("VALUE = 1\n", encoding="utf-8")
                return {"summary": "feature on new base", "questions": []}

            second = run_change(root, feature_id, LoopServices(
                generate_smoke=lambda project, change: self.fail("locked suite must be reused"),
                implement=implement, review=review))
            self.assertEqual(second["status"], "PASSED")
            state = load_run_state(root, feature_id)
            new_red = load_json(root / ".storyloop/evidence" / feature_id / RED_EVIDENCE)
            self.assertNotEqual(new_red["head_commit"], old_red["head_commit"])
            self.assertEqual(new_red["head_commit"], state["contract_commit"])
            self.assertEqual(load_json(root / ".storyloop/state/changes" / f"{base_id}.json")["status"],
                             "PASSED")
