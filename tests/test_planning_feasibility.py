"""Committed principles and narrowly trusted offline dependency evidence."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from storyloop.models import StoryLoopError
from storyloop.planning import assess_plan
from storyloop.principles import load_principles
from tests import test_routing_planning
from tests.test_baseline_behavior import git


class PrincipleTests(unittest.TestCase):
    setUp = test_routing_planning.PlanningTests.setUp
    plan = test_routing_planning.PlanningTests.plan
    ensure = test_routing_planning.PlanningTests.ensure

    def configure(self, value=None):
        path = self.root / 'openspec/principles.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(value or {'version': 1, 'principles': [
            {'id': 'P1', 'text': 'Preserve module dependency direction'}]}) + '\n')
        git(self.root, 'add', 'openspec/principles.json')
        git(self.root, 'commit', '-qm', 'principles')
        source = git(self.root, 'rev-parse', 'HEAD').strip()
        trusted = patch('storyloop.principles.load_baseline', return_value={'source_head': source})
        trusted.start()
        self.addCleanup(trusted.stop)
        return path

    def response(self, verdict='complies'):
        response = test_routing_planning.answer()
        response['options'][0]['principle_checks'] = [
            {'principle_id': 'P1', 'verdict': verdict, 'reason': 'Explicit design tradeoff'}]
        return response

    def test_no_principles_is_explicit_not_configured(self):
        self.assertEqual(load_principles(self.root)['status'], 'NOT_CONFIGURED')
        self.ensure()
        self.assertIn('principles_digest', self.state['planning']['plan']['binding'])
        self.assertIsNone(self.state['planning']['plan']['binding']['principles_digest'])

    def test_declared_deviation_forces_heavy_despite_risk_no(self):
        self.configure()
        self.state['routing'].update(initial_tier='standard', effective_tier='standard')
        self.ensure(lambda p, c: self.response('deviates'))
        assessed = self.state['planning']['assessment']
        self.assertEqual(assessed['signals']['risks']['principle_deviation'], 'yes')
        self.assertEqual(self.state['routing']['effective_tier'], 'heavy')
        self.assertTrue(assessed['principles']['deviations'])
        plan = self.plan(self.response())
        self.assertEqual(assess_plan(self.root, self.change, self.state, plan)[
            'signals']['risks']['principle_deviation'], 'yes')

    def test_missing_unknown_or_duplicate_principle_references_rejected(self):
        self.configure()
        for checks in ([], [{'principle_id': 'wrong', 'verdict': 'complies', 'reason': 'test'}]):
            response = self.response()
            response['options'][0]['principle_checks'] = checks
            with self.assertRaises(StoryLoopError):
                self.plan(response)

    def test_unknown_principle_assessment_stops_for_input(self):
        self.configure()
        self.state['routing'].update(initial_tier='standard', effective_tier='standard')
        self.assertEqual(self.ensure(lambda p, c: self.response('unknown'))['status'], 'NEEDS_INPUT')

    def test_light_with_principles_uses_design_instead_of_omitting_checks(self):
        self.configure()
        calls = []
        self.ensure(lambda p, c: calls.append(c) or self.response('deviates'))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.state['routing']['effective_tier'], 'heavy')

    def test_committed_source_version_uniqueness_and_drift(self):
        path = self.configure()
        first = load_principles(self.root)['digest']
        path.write_text(path.read_text().replace('direction', 'boundaries'))
        with self.assertRaises(StoryLoopError):
            load_principles(self.root)
        git(self.root, 'add', 'openspec/principles.json')
        git(self.root, 'commit', '-qm', 'principles changed')
        with self.assertRaisesRegex(StoryLoopError, 'trusted baseline'):
            load_principles(self.root)
        self.configure({'version': 1, 'principles': [{'id': 'P1', 'text': 'New approved base'}]})
        self.assertNotEqual(first, load_principles(self.root)['digest'])
        for value in ({'version': True, 'principles': []},
                      {'version': 2, 'principles': []},
                      {'version': 1, 'principles': [{'id': 'X', 'text': 'x'}] * 2}):
            self.configure(value)
            with self.assertRaises(StoryLoopError):
                load_principles(self.root)


class DependencyTests(unittest.TestCase):
    def setUp(self):
        from tests.routing_support import readiness_repository
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        readiness_repository(self.root)
        self.wheel()
        self.commit = git(self.root, 'rev-parse', 'HEAD').strip()
        self.state = {'contract_commit': self.commit}

    def wheel(self, metadata_extra='', tag='py3-none-any'):
        import zipfile
        directory = self.root / 'vendor'
        directory.mkdir(exist_ok=True)
        with zipfile.ZipFile(directory / 'demo-1.0-py3-none-any.whl', 'w') as archive:
            archive.writestr('demo-1.0.dist-info/METADATA',
                             'Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n' + metadata_extra)
            archive.writestr('demo-1.0.dist-info/WHEEL',
                             'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: ' + tag + '\n')
        git(self.root, 'add', 'vendor')
        git(self.root, 'commit', '-qm', 'local wheel')

    def configured(self):
        import sys
        from storyloop import dependency_resolver
        from storyloop.baseline import load_project_config
        from storyloop.storage import write_json
        argv = [sys.executable, '-I', str(Path(dependency_resolver.__file__).resolve()),
                '--wheel', 'vendor/demo-1.0-py3-none-any.whl', '--cache-dir', '.cache/resolver',
                '--output', '.cache/resolver/result.json']
        config = load_project_config(self.root)
        config['profile']['check_commands'] = [argv]
        write_json(self.root / '.storyloop/config.json', config)
        return argv

    def dependencies(self):
        return [{'ecosystem': 'python', 'name': 'demo', 'version': '1.0',
                 'verification': {'check_index': 0}}]

    def verify(self, dependencies=None):
        from storyloop.dependency_checks import verify_dependencies
        return verify_dependencies(self.root, self.state,
                                   self.dependencies() if dependencies is None else dependencies)

    def test_supported_wheel_uses_committed_bytes_and_bound_command(self):
        self.configured()
        (self.root / 'vendor/demo-1.0-py3-none-any.whl').read_bytes()
        first = self.verify()
        self.assertEqual(first[0]['resolved'], [{'ecosystem': 'python', 'name': 'demo', 'version': '1.0'}])
        self.assertTrue(first[0]['command_digest'])
        self.assertEqual(first[0]['source_commit'], self.commit)
        (self.root / 'vendor/demo-1.0-py3-none-any.whl').write_bytes(b'candidate pollution')
        self.assertEqual(self.verify(), first)
        self.assertFalse((self.root / '.cache/resolver').exists())

    def test_no_dependency_needs_no_command_and_unsupported_never_executes(self):
        from storyloop.baseline import load_project_config
        from storyloop.storage import write_json
        self.assertEqual(self.verify([]), [])
        with self.assertRaises(StoryLoopError) as caught:
            self.verify()
        self.assertEqual(caught.exception.code, 'NEEDS_INPUT')
        config = load_project_config(self.root)
        config['profile']['check_commands'] = [['sh', '-c', 'touch escaped']]
        write_json(self.root / '.storyloop/config.json', config)
        with patch('storyloop.dependency_checks.run_process') as run:
            with self.assertRaises(StoryLoopError) as caught:
                self.verify()
            self.assertEqual(caught.exception.code, 'NEEDS_INPUT')
            run.assert_not_called()
        self.assertFalse((self.root / 'escaped').exists())

    def test_index_bool_wrong_version_and_unsupported_ecosystem_fail(self):
        self.configured()
        for mutation in ({'verification': {'check_index': True}},
                         {'verification': {'check_index': 8}}, {'version': '2.0'},
                         {'ecosystem': 'npm'}):
            dependency = {**self.dependencies()[0], **mutation}
            with self.subTest(mutation=mutation), self.assertRaises(StoryLoopError) as caught:
                self.verify([dependency])
            self.assertEqual(caught.exception.code, 'NEEDS_INPUT')

    def test_transitive_or_platform_wheels_fail_closed(self):
        self.configured()
        for extra, tag in [('Requires-Dist: other>=1\n', 'py3-none-any'),
                           ('', 'cp311-cp311-linux_x86_64')]:
            self.wheel(extra, tag)
            self.state['contract_commit'] = git(self.root, 'rev-parse', 'HEAD').strip()
            with self.assertRaises(StoryLoopError) as caught:
                self.verify()
            self.assertEqual(caught.exception.code, 'NEEDS_INPUT')

    def test_missing_failed_or_forged_process_evidence_rejected(self):
        self.configured()
        for result in ({'returncode': 1},
                       {'returncode': 0, 'contained': True, 'stdout': '{}'},
                       {'returncode': 0, 'contained': True, 'stdout': json.dumps({'resolved': [
                           {'ecosystem': 'python', 'name': 'demo', 'version': '9.0'}]})}):
            with self.subTest(result=result), patch('storyloop.dependency_checks.run_process',
                                                   return_value=result):
                with self.assertRaises(StoryLoopError) as caught:
                    self.verify()
                self.assertEqual(caught.exception.code, 'NEEDS_INPUT')
