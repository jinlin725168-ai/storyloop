"""Readiness gates use actual execution and approval bound to current evidence."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.models import StoryLoopError
from storyloop.readiness import (approve_readiness, inspect_readiness, readiness_status,
                                require_readiness, _require_readiness_locked)
from storyloop.storage import load_json, locked, write_json
from tests.routing_support import readiness_repository
from tests.test_baseline_behavior import git


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        readiness_repository(self.project)

    def config(self, **profile):
        path = self.project / '.storyloop/config.json'
        config = load_json(path)
        config['profile'].update(profile)
        write_json(path, config)

    def test_gap_approval_cannot_waive_failed_tests(self):
        layers = {name: {'status': 'MISSING'} for name in ('unit', 'integration', 'checks', 'smoke')}
        layers['unit'] = {'status': 'FAILED', 'executed': 1}
        self.assertEqual(readiness_status(layers, ['checks', 'smoke']), 'PROJECT_NOT_READY')

    def test_real_count_and_explicit_gap_approval(self):
        record = inspect_readiness(self.project)
        self.assertEqual(record['layers']['unit']['executed'], 1)
        self.assertIsNone(record['layers']['unit']['discovered'])
        self.assertEqual(record['status'], 'READINESS_APPROVAL_REQUIRED')
        with self.assertRaises(StoryLoopError) as caught:
            require_readiness(self.project)
        self.assertEqual(caught.exception.details, record)
        ready = approve_readiness(self.project, record['digest'], 'small local project')
        self.assertEqual(ready['status'], 'READY')
        self.assertEqual(ready['layers']['checks']['status'], 'MISSING')
        self.assertEqual(ready, approve_readiness(self.project, record['digest'], 'same decision'))
        self.assertFalse((self.project / '.storyloop/state/runs').exists())

    def test_all_missing_and_unexecuted_tests_are_blockers(self):
        commands = [[], [['{python}', '-c', "print('Ran 0 tests in 0.001s\\nOK')"]],
                    [['{python}', '-c', "print('Ran 1 test in 0.001s\\nOK (skipped=1)')"]],
                    [['{python}', '-c', "print('unknown summary')"]]]
        for value in commands:
            with self.subTest(commands=value):
                self.config(unit_commands=value)
                self.assertEqual(inspect_readiness(self.project)['status'], 'PROJECT_NOT_READY')

    def test_checks_failure_cannot_be_approved(self):
        record = inspect_readiness(self.project)
        approve_readiness(self.project, record['digest'], 'missing checks accepted')
        self.config(check_commands=[['{python}', '-c', 'raise SystemExit(1)']])
        changed = inspect_readiness(self.project)
        self.assertEqual(changed['status'], 'PROJECT_NOT_READY')
        with self.assertRaises(StoryLoopError):
            approve_readiness(self.project, changed['digest'], 'ignore failure')

    def test_stale_digest_and_blank_reason_rejected(self):
        record = inspect_readiness(self.project)
        with self.assertRaises(StoryLoopError):
            approve_readiness(self.project, record['digest'], '  ')
        self.config(check_commands=[['{python}', '-c', 'pass']])
        with self.assertRaises(StoryLoopError):
            approve_readiness(self.project, record['digest'], 'stale')

    def test_cache_and_locked_entrypoint(self):
        record = inspect_readiness(self.project)
        ready = approve_readiness(self.project, record['digest'], 'reviewed')
        with patch('storyloop.readiness.run_commands_at_commit', side_effect=AssertionError('cache missed')):
            self.assertEqual(inspect_readiness(self.project), ready)
            with locked(self.project):
                self.assertEqual(_require_readiness_locked(self.project), ready)
                with self.assertRaisesRegex(StoryLoopError, 'lock'):
                    inspect_readiness(self.project)

    def test_dirty_source_cannot_reuse_cache(self):
        record = inspect_readiness(self.project)
        approve_readiness(self.project, record['digest'], 'reviewed')
        (self.project / 'app/service.py').write_text('changed = True\n')
        with self.assertRaises(StoryLoopError):
            inspect_readiness(self.project)

    def test_absolute_source_write_is_detected_before_save(self):
        target = self.project / 'app/service.py'
        command = ['{python}', '-c', f"from pathlib import Path; Path({str(target)!r}).write_text('polluted')"]
        self.config(unit_commands=[command])
        with self.assertRaises(StoryLoopError) as caught:
            inspect_readiness(self.project)
        self.assertEqual(caught.exception.code, 'WORKSPACE_TAINTED')
        self.assertFalse((self.project / '.storyloop/state/readiness.json').exists())

    def test_isolation_keeps_relative_writes_out_of_source(self):
        self.config(check_commands=[['{python}', '-c', "open('local.tmp', 'w').write('candidate')"]])
        self.assertEqual(inspect_readiness(self.project)['layers']['checks']['status'], 'PASSED')
        self.assertFalse((self.project / 'local.tmp').exists())

    def test_one_uncounted_command_blocks_other_positive_counts(self):
        self.config(integration_commands=[['{python}', '-c', 'pass']])
        record = inspect_readiness(self.project)
        self.assertEqual(record['status'], 'PROJECT_NOT_READY')
        self.assertIn('integration', record['blockers'])

    def test_timeout_is_a_nonwaivable_failure(self):
        path = self.project / '.storyloop/config.json'
        config = load_json(path)
        config['check_timeout_seconds'] = 1
        config['profile']['check_commands'] = [['{python}', '-c', 'import time; time.sleep(10)']]
        write_json(path, config)
        record = inspect_readiness(self.project)
        self.assertEqual(record['status'], 'PROJECT_NOT_READY')
        self.assertTrue(record['layers']['checks']['results'][0]['timed_out'])

    def test_protected_smoke_counts_and_drift(self):
        from storyloop.system_smoke import install_system_smoke
        files = [{'path': 'test_core.py', 'content':
                  'import unittest\nclass Core(unittest.TestCase):\n'
                  '    def test_core(self): self.assertTrue(True)\n'
                  "if __name__ == '__main__': unittest.main()\n"}]
        manifest = {'version': 1, 'tests': [{'id': 'core', 'description': 'core flow',
                    'command': ['{python}', 'acceptance/smoke/test_core.py'], 'timeout_seconds': 10}]}
        install_system_smoke(self.project, files, manifest)
        git(self.project, 'add', 'acceptance')
        git(self.project, 'commit', '-q', '-m', 'install system smoke')
        self.config(check_commands=[['{python}', '-c', 'pass']])
        record = inspect_readiness(self.project)
        self.assertEqual(record['status'], 'READY')
        self.assertEqual(record['layers']['smoke']['executed'], 1)
        self.assertTrue(record['binding']['suite_digests'])
        (self.project / 'acceptance/smoke/test_core.py').write_text('pass\n')
        git(self.project, 'add', 'acceptance')
        git(self.project, 'commit', '-q', '-m', 'drift')
        with self.assertRaisesRegex(StoryLoopError, 'modified'):
            inspect_readiness(self.project)

    def test_approval_recollects_even_with_unchanged_binding(self):
        record = inspect_readiness(self.project)
        failed = {'status': 'FAILED', 'results': [{'id': 'REGRESSION-001', 'status': 'FAIL',
                  'counts': {'executed': 1, 'failed': 1, 'skipped': 0}, 'returncode': 1}]}
        with patch('storyloop.readiness.run_commands_at_commit', return_value=failed):
            with self.assertRaises(StoryLoopError):
                approve_readiness(self.project, record['digest'], 'cannot accept fresh failure')
        self.assertEqual(load_json(self.project / '.storyloop/state/readiness.json')['status'],
                         'PROJECT_NOT_READY')

    def test_uses_full_counts_not_retained_output(self):
        outcome = {'status': 'PASSED', 'results': [{'id': 'REGRESSION-001', 'status': 'PASS',
                   'counts': {'executed': 7, 'failed': 0, 'skipped': 2, 'parser': 'unittest'},
                   'stdout': 'truncated before final summary', 'stderr': '', 'returncode': 0}]}
        with patch('storyloop.readiness.run_commands_at_commit', return_value=outcome):
            record = inspect_readiness(self.project)
        self.assertEqual(record['layers']['unit']['executed'], 7)
        self.assertNotIn('stdout', str(record))

    def test_control_plane_write_is_detected(self):
        target = self.project / '.storyloop/config.json'
        command = ['{python}', '-c', f"from pathlib import Path; Path({str(target)!r}).write_text('{{}}')"]
        self.config(unit_commands=[command])
        with self.assertRaises(StoryLoopError) as caught:
            inspect_readiness(self.project)
        self.assertEqual(caught.exception.code, 'WORKSPACE_TAINTED')
        self.assertFalse((self.project / '.storyloop/state/readiness.json').exists())

    def test_even_telemetry_control_changes_are_rejected(self):
        target = self.project / '.storyloop/state/telemetry/event.json'
        target.parent.mkdir(parents=True)
        target.write_text('{}')
        command = ['{python}', '-c', f"from pathlib import Path; Path({str(target)!r}).write_text('{{\"changed\":true}}')"]
        self.config(unit_commands=[command])
        with self.assertRaises(StoryLoopError) as caught:
            inspect_readiness(self.project)
        self.assertEqual(caught.exception.code, 'WORKSPACE_TAINTED')

    def test_scratch_configuration_cannot_hide_control_changes(self):
        target = self.project / '.storyloop/state/injected.json'
        command = ['{python}', '-c', f"from pathlib import Path; Path({str(target)!r}).write_text('{{}}')"]
        self.config(unit_commands=[command], scratch_paths=['.storyloop'])
        (self.project / '.storyloop/state').mkdir(exist_ok=True)
        with self.assertRaises(StoryLoopError) as caught:
            inspect_readiness(self.project)
        self.assertEqual(caught.exception.code, 'WORKSPACE_TAINTED')
