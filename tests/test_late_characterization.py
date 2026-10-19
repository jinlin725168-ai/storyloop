"""Late locks prove old behavior without discarding implementation-authored tests."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from storyloop.project_loop import run_change, load_run_state
from tests.routing_support import routed_case, routed_product
from tests.test_routing_planning import answer


FEATURE_TEST = ('\nclass NewBehavior(unittest.TestCase):\n'
                '    def test_new_value(self):\n'
                '        import runpy\n'
                '        self.assertEqual(runpy.run_path("app/service.py")["NEW"], 1)\n')


class LateCharacterizationTests(unittest.TestCase):
    def case(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        change, services = routed_case(root, 'standard')
        original = services.implement
        def implement(project, context):
            response = original(project, context)
            target = project / 'app/service.py'
            target.write_text('def serve():\n    return "ok"\nNEW = 1\n', encoding='utf-8')
            existing = project / 'tests/test_service.py'
            if 'class NewBehavior' not in existing.read_text():
                existing.write_text(existing.read_text() + FEATURE_TEST, encoding='utf-8')
            (project / 'tests/test_new.py').write_text('import unittest\n' + FEATURE_TEST, encoding='utf-8')
            return response
        plan = answer(routed_product())
        plan['options'][0]['touched'].append('tests/test_service.py')
        plan['options'][0]['creates'].append('tests/test_new.py')
        return root, change['change_id'], replace(services, implement=implement, design=lambda p, c: plan)

    def test_new_and_modified_feature_tests_survive_old_behavior_lock(self):
        root, identifier, services = self.case()
        result = run_change(root, identifier, services)
        self.assertEqual(result['status'], 'PASSED', result)
        self.assertIn(FEATURE_TEST, (root / 'tests/test_service.py').read_text())
        self.assertIn(FEATURE_TEST, (root / 'tests/test_new.py').read_text())
        self.assertEqual(load_run_state(root, identifier)['attempt'], 2)

    def test_actual_red_lock_blocks_then_resume_preserves_feature_tests(self):
        root, identifier, services = self.case()
        original = services.characterize
        def characterize(project, context):
            answer = original(project, context)
            test = project / answer['tests'][0]
            test.write_text(test.read_text().replace('"ok"', '"wrong"'), encoding='utf-8')
            return answer
        result = run_change(root, identifier, replace(services, characterize=characterize))
        self.assertEqual(result['status'], 'CHARACTERIZATION_FAILED')
        self.assertIn(FEATURE_TEST, (root / 'tests/test_service.py').read_text())
        lock = root / 'tests/test_lock.py'
        lock.write_text(lock.read_text().replace('"wrong"', '"ok"'), encoding='utf-8')
        result = run_change(root, identifier, services)
        self.assertEqual(result['status'], 'PASSED', result)
        self.assertIn(FEATURE_TEST, (root / 'tests/test_new.py').read_text())

    def test_feature_assertion_inside_lock_artifact_is_not_filtered(self):
        root, identifier, services = self.case()
        original = services.characterize
        def characterize(project, context):
            answer = original(project, context)
            test = project / answer['tests'][0]
            test.write_text(test.read_text() + FEATURE_TEST, encoding='utf-8')
            return answer
        result = run_change(root, identifier, replace(services, characterize=characterize))
        self.assertEqual(result['status'], 'CHARACTERIZATION_FAILED')
        self.assertIn('NEW', str(result['unit']['results']))
        self.assertIn(FEATURE_TEST, (root / 'tests/test_new.py').read_text())

class CharacterizationCandidateSafetyTests(unittest.TestCase):
    def test_non_test_artifact_cannot_overlay_business_code(self):
        from storyloop.characterization_baseline import run_characterization_baseline
        from storyloop.models import StoryLoopError
        from tests.routing_support import readiness_repository
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            readiness_repository(root)
            with self.assertRaises(StoryLoopError):
                run_characterization_baseline(root, [], 10,
                    {'tests': ['app/service.py'], 'changed': []})

    def test_linked_artifact_rejected_without_following_it(self):
        from storyloop.characterization_baseline import run_characterization_baseline
        from storyloop.models import StoryLoopError
        from tests.routing_support import readiness_repository
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            readiness_repository(root)
            try:
                (root / 'tests/alias.py').symlink_to(root / 'app/service.py')
            except OSError:
                self.skipTest('Symlinks unavailable')
            with self.assertRaises(StoryLoopError):
                run_characterization_baseline(root, [], 10,
                    {'tests': ['tests/alias.py'], 'changed': []})

    def test_committed_link_is_rejected_not_silently_omitted(self):
        from storyloop.characterization_baseline import run_characterization_baseline
        from storyloop.models import StoryLoopError
        from tests.routing_support import readiness_repository
        from tests.test_baseline_behavior import git
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            readiness_repository(root)
            try:
                (root / 'tests/alias.py').symlink_to('../app/service.py')
            except OSError:
                self.skipTest('Symlinks unavailable')
            git(root, 'add', 'tests/alias.py')
            git(root, 'commit', '-qm', 'linked baseline fixture')
            with self.assertRaises(StoryLoopError):
                run_characterization_baseline(root, [], 10, {'tests': [], 'changed': []})
