"""Independent acceptance checks for linked base collision hints."""
import json
import os
import shlex
import subprocess
import sys
import unittest
from pathlib import Path

from storyloop.testing.smoke_harness.v1 import HarnessError


RED_PROBE = r'''
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from tests.test_failure_routing import (NOW, approve_change, create_change, product,
                                        repository, write_json)
from tests.test_project_loop import smoke_payload
from tests.test_baseline_behavior import git
from storyloop.baseline import bootstrap_project, load_project_config
from storyloop.project_loop import LoopServices, load_run_state, run_change
from storyloop.stop_report import stop_of

def public(command, root, change_id, env=None):
    child = subprocess.run([sys.executable, '-m', 'storyloop', command, str(root),
                            '--change', change_id, '--json', '--no-interactive']
                           if command == 'run' else
                           [sys.executable, '-m', 'storyloop', command, str(root),
                            '--change', change_id, '--json'],
                           cwd=Path.cwd(), env=env, capture_output=True, text=True, timeout=30)
    if not child.stdout.strip():
        raise RuntimeError(command + ' CLI failed: ' + child.stderr[-1000:])
    return json.loads(child.stdout)

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder, 'project with space')
    root.mkdir()
    repository(root)
    (root / 'pom.xml').write_text('<project>old</project>\n', encoding='utf-8')
    git(root, 'add', 'pom.xml')
    git(root, 'commit', '-m', 'pom')
    for name in ('tests/test_changed.py', 'tests/test_removed.py'):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'VALUE = 1\n')
        git(root, 'add', name)
    git(root, 'commit', '-m', 'foundation fixtures')
    bootstrap_project(root, {'test_commands': [], 'test_roots': ['tests']})
    config = load_project_config(root)
    config['max_attempts'] = 3
    write_json(root / '.storyloop/config.json', config)
    change_id = create_change(root, 'orders', product(), now=lambda: NOW)['change_id']
    approve_change(root, change_id, now=lambda: NOW)
    expected = ['modules/orders/service.py', 'tests/test_changed.py',
                'tests/test_removed.py']
    calls = []

    def implement(project, context):
        calls.append(context['attempt'])
        if context['attempt'] == 1:
            feature = project / expected[0]
            feature.parent.mkdir(parents=True, exist_ok=True)
            feature.write_text('VALUE = 2\n', encoding='utf-8')
            (project / expected[1]).write_text('VALUE = 2\n', encoding='utf-8')
            (project / expected[2]).unlink()
        else:
            (project / 'pom.xml').write_text('<project>new</project>\n', encoding='utf-8')
        return {'summary': 'candidate', 'questions': []}

    result = run_change(root, change_id, LoopServices(
        generate_smoke=lambda project, state: smoke_payload(change_id),
        implement=implement,
        review=lambda project, context: {
            'verdict': 'fail', 'summary': 'try again',
            'findings': [], 'questions': [],
        }))
    state = load_run_state(root, change_id)
    report_file = root / 'reports/storyloop' / change_id / 'report.md'
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(stop_of(root, state)['options'][0]['action'], encoding='utf-8')
    status = public('status', root, change_id)
    log = public('log', root, change_id)
    report = public('report', root, change_id)
    report_action = Path(report['report']).read_text(encoding='utf-8')
    report_file.unlink()
    stub_dir = Path(folder, 'agent-stubs')
    stub_dir.mkdir()
    marker = stub_dir / 'called'
    for name in ('codex', 'claude'):
        stub = stub_dir / name
        stub.write_text('#!/bin/sh\necho called > ' + shlex.quote(str(marker)) + '\nexit 91\n')
        stub.chmod(0o755)
    rerun = public('run', root, change_id,
                   env={**os.environ, 'PATH': str(stub_dir) + os.pathsep + os.environ.get('PATH', '')})
    before = git(root, 'status', '--porcelain=v1', '--untracked-files=all')
    segment = result['next'].split('先用 ', 1)[1].split(' 保存', 1)[0] if '先用 ' in result['next'] else ''
    tokens = shlex.split(segment) if segment else []
    executed = subprocess.run(tokens, cwd=folder, capture_output=True, text=True,
                              timeout=15) if tokens else None
    after = git(root, 'status', '--porcelain=v1', '--untracked-files=all')
    stash_paths = (git(root, 'stash', 'show', '-u', '--name-only', 'stash@{0}').splitlines()
                   if executed is not None and executed.returncode == 0 else [])
    print(json.dumps({
        'root': str(root), 'calls': calls, 'status': result['status'],
        'rerun_status': rerun['status'], 'rerun_next': rerun.get('next'),
        'rerun_action': rerun.get('stop', {}).get('options', [{}])[0].get('action'),
        'agent_stub_called': marker.exists(),
        'expected': expected, 'legacy_paths': state.get('legacy_paths', []),
        'run_legacy_paths': result.get('legacy_paths', []),
        'surfaces': [result['next'], result['stop']['options'][0]['action'],
                     status['next'], status['stop']['options'][0]['action'],
                     log['next'], log['stop']['options'][0]['action'],
                     report_action],
        'stash_tokens': tokens, 'stash_exit': executed.returncode if executed else None,
        'stash_paths': stash_paths, 'before': before, 'after': after,
    }, ensure_ascii=False))
'''

GREEN_PROBE = r'''
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from storyloop.baseline import bootstrap_project
from storyloop.run_hints import next_step
from storyloop.run_stops import pause
from storyloop.stop_report import stop_of


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                            text=True, timeout=10)
    if result.returncode:
        raise RuntimeError('Git fixture failed: ' + result.stderr)
    return result.stdout

def public(command, root, change_id):
    child = subprocess.run([sys.executable, '-m', 'storyloop', command, str(root),
                            '--change', change_id, '--json'], cwd=Path.cwd(),
                           capture_output=True, text=True, timeout=20)
    if not child.stdout.strip():
        raise RuntimeError(command + ' CLI failed: ' + child.stderr[-1000:])
    return json.loads(child.stdout)

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder, 'project with space')
    root.mkdir()
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'Acceptance')
    git(root, 'config', 'user.email', 'acceptance@example.invalid')
    (root / '.gitignore').write_text('.storyloop/\n', encoding='utf-8')
    git(root, 'add', '.gitignore')
    git(root, 'commit', '-qm', 'base fixture')
    bootstrap_project(root, {'test_commands': []})
    listed = ['ordinary.py', '[ab].py', ':(exclude)other.py']
    unlisted = ['a.py', 'other.py', 'unlisted.py']
    for name in listed + unlisted:
        (root / name).write_text('before:' + name + '\n', encoding='utf-8')
    git(root, '--literal-pathspecs', 'add', '--', '.gitignore', *listed, *unlisted)
    git(root, 'commit', '-qm', 'fixture')
    for name in listed + unlisted:
        (root / name).write_text('after:' + name + '\n', encoding='utf-8')
    details = {'blocked_by': 'BASE-1', 'legacy_paths': listed,
               'source_branch': 'main', 'feature_branch': 'feature/FEAT-1'}
    state = {'change_id': 'FEAT-1', 'status': 'IMPLEMENTING',
             'attempt': 1, 'failures': []}
    stash_before_pause = git(root, 'stash', 'list')
    run = pause(root, state, 'BASE_CHANGE_REQUIRED', **details)
    stash_after_pause = git(root, 'stash', 'list')
    product = {'version': 1, 'goal': 'Reserve a meeting room without overlap',
               'non_goals': [], 'questions': [], 'stories': [{
                   'id': 'ST-1', 'title': 'Reserve a room', 'as_a': 'employee',
                   'want': 'reserve a free room', 'so_that': 'meetings do not collide',
                   'rules': ['No overlap'], 'out_of_scope': [], 'depends_on': [],
                   'acceptance': [{'id': 'AC-1', 'given': 'a reservation exists',
                                   'when': 'another overlaps', 'then': 'reject it',
                                   'kind': 'automated'}]}]}
    change = {'version': 1, 'change_id': 'FEAT-1', 'status': 'APPROVED',
              'revision': 1, 'idea': 'fixture', 'product': product,
              'created_at': '2026-01-01T00:00:00Z',
              'updated_at': '2026-01-01T00:00:00Z', 'history': [],
              'approval': None}
    change_path = root / '.storyloop/state/changes/FEAT-1.json'
    change_path.parent.mkdir(parents=True, exist_ok=True)
    change_path.write_text(json.dumps(change), encoding='utf-8')
    run_path = root / '.storyloop/state/runs/FEAT-1.json'
    report_file = root / 'reports/storyloop/FEAT-1/report.md'
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(stop_of(root, json.loads(run_path.read_text(encoding='utf-8'))
                                )['options'][0]['action'], encoding='utf-8')
    saved_before = run_path.read_bytes().hex()
    work_before = git(root, 'status', '--porcelain=v1', '--untracked-files=all')
    stash_before = git(root, 'stash', 'list')
    status = public('status', root, 'FEAT-1')
    log = public('log', root, 'FEAT-1')
    report = public('report', root, 'FEAT-1')
    saved_after = run_path.read_bytes().hex()
    work_after = git(root, 'status', '--porcelain=v1', '--untracked-files=all')
    stash_after = git(root, 'stash', 'list')
    command = run['next'].split('先用 ', 1)[1].split(' 保存', 1)[0]
    tokens = shlex.split(command)
    unlisted_before = {name: (root / name).read_text(encoding='utf-8')
                       for name in unlisted}
    executed = subprocess.run(tokens, cwd=folder, capture_output=True,
                              text=True, timeout=10)
    stash_paths = (git(root, 'stash', 'show', '-u', '--name-only', 'stash@{0}').splitlines()
                   if executed.returncode == 0 else [])
    print(json.dumps({
        'root': str(root), 'listed': listed, 'run_next': run['next'],
        'run_action': run['stop']['options'][0]['action'],
        'approval': run['stop']['options'][0]['command'],
        'empty_hint': next_step('BASE_CHANGE_REQUIRED', 'FEAT-1', root,
                                {**details, 'legacy_paths': []}),
        'status_next': status['next'], 'log_next': log['next'],
        'report_action': Path(report['report']).read_text(encoding='utf-8'),
        'saved_before': saved_before, 'saved_after': saved_after,
        'work_before': work_before, 'work_after': work_after,
        'stash_before': stash_before, 'stash_after': stash_after,
        'stash_before_pause': stash_before_pause,
        'stash_after_pause': stash_after_pause,
        'stash_tokens': tokens, 'stash_exit': executed.returncode,
        'stash_paths': stash_paths,
        'unlisted_before': unlisted_before,
        'unlisted_after': {name: (root / name).read_text(encoding='utf-8')
                           for name in unlisted},
        'listed_after': {name: (root / name).read_text(encoding='utf-8')
                         for name in listed},
    }, ensure_ascii=False))
'''


def probe(source):
    try:
        child = subprocess.run([sys.executable, '-c', source], cwd=Path.cwd(),
                               capture_output=True, text=True, timeout=90)
        if child.returncode:
            raise HarnessError('Business subprocess failed: ' + child.stderr[-1500:])
        value = json.loads(child.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        raise HarnessError('Business subprocess/protocol unavailable') from error
    required = ({'expected', 'legacy_paths', 'run_legacy_paths', 'surfaces',
                 'stash_tokens', 'stash_exit', 'stash_paths', 'before', 'after',
                 'root', 'calls', 'status', 'rerun_status', 'rerun_next',
                 'rerun_action', 'agent_stub_called'} if source == RED_PROBE else
                {'root', 'listed', 'run_next', 'run_action', 'approval', 'empty_hint',
                 'status_next', 'log_next', 'report_action', 'saved_before',
                 'saved_after', 'work_before', 'work_after', 'stash_before',
                 'stash_after', 'stash_before_pause', 'stash_after_pause',
                 'stash_tokens', 'stash_exit', 'stash_paths', 'unlisted_before',
                 'unlisted_after', 'listed_after'})
    if not isinstance(value, dict) or set(value) != required:
        raise HarnessError('Business protocol is invalid')
    return value


class DefectReproduction(unittest.TestCase):
    def test_ac2_all_views_include_the_same_function_and_test_paths(self):
        value = probe(RED_PROBE)
        self.assertEqual(value['status'], 'BASE_CHANGE_REQUIRED')
        self.assertEqual(value['calls'], [1, 2])
        self.assertEqual(set(value['run_legacy_paths']), set(value['expected']))
        self.assertEqual(set(value['legacy_paths']), set(value['expected']))
        expected_stash = ['git', '-C', value['root'], '--literal-pathspecs',
                          'stash', 'push', '-u', '--', *sorted(value['expected'])]
        for surface in value['surfaces']:
            with self.subTest(surface=surface):
                self.assertIn(shlex.join(expected_stash), surface)
                self.assertIn(shlex.join(['git', '-C', value['root'], 'status', '--short']),
                              surface)
                self.assertIn(shlex.join(['git', '-C', value['root'], 'switch']), surface)

    def test_ac7_stash_clears_modified_and_deleted_tests_as_well_as_feature(self):
        value = probe(RED_PROBE)
        self.assertEqual(value['status'], 'BASE_CHANGE_REQUIRED')
        self.assertEqual(value['calls'], [1, 2])
        self.assertEqual(set(value['legacy_paths']), set(value['expected']))
        self.assertTrue(value['before'])
        self.assertEqual(value['stash_tokens'][:8],
                         ['git', '-C', value['root'], '--literal-pathspecs',
                          'stash', 'push', '-u', '--'])
        self.assertEqual(set(value['stash_tokens'][8:]), set(value['expected']))
        self.assertEqual(value['stash_exit'], 0)
        self.assertEqual(set(value['stash_paths']), set(value['expected']))
        self.assertEqual(value['after'], '')


class ExistingBehavior(unittest.TestCase):
    def test_run_at_base_stop_repeats_stop_without_agent(self):
        value = probe(RED_PROBE)
        self.assertEqual(value['rerun_status'], 'BASE_CHANGE_REQUIRED')
        self.assertEqual(value['rerun_action'], value['surfaces'][1])
        self.assertEqual(value['rerun_next'], value['surfaces'][0])
        self.assertFalse(value['agent_stub_called'])

    def test_ac1_stash_uses_literal_paths_in_project_from_outside(self):
        value = probe(GREEN_PROBE)
        self.assertEqual(value['stash_tokens'],
                         ['git', '-C', value['root'], '--literal-pathspecs',
                          'stash', 'push', '-u', '--', *value['listed']])
        self.assertEqual(value['stash_exit'], 0)
        self.assertEqual(set(value['stash_paths']), set(value['listed']))
        self.assertEqual(value['unlisted_after'], value['unlisted_before'])
        self.assertEqual(value['listed_after'],
                         {name: 'before:' + name + '\n' for name in value['listed']})

    def test_ac3_no_legacy_paths_means_repo_bound_switch_without_stash(self):
        value = probe(GREEN_PROBE)
        hint = value['empty_hint']
        self.assertNotIn('stash push', hint)
        self.assertIn(shlex.join(['git', '-C', value['root'], 'switch', 'main']), hint)
        self.assertIn(shlex.join(['git', '-C', value['root'], 'switch',
                                   'feature/FEAT-1']), hint)

    def test_ac4_approval_and_human_step_order_remain(self):
        value = probe(GREEN_PROBE)
        self.assertEqual(shlex.split(value['approval']),
                         ['storyloop', 'approve', value['root'], '--change', 'BASE-1'])
        markers = ['stash push', 'status --short', 'switch main',
                   'storyloop approve', 'storyloop run',
                   'switch feature/FEAT-1', '取回或丢弃暂存']
        for surface in (value['run_next'], value['run_action'], value['report_action']):
            positions = [surface.index(marker) for marker in markers]
            self.assertEqual(positions, sorted(positions))

    def test_ac5_reading_status_log_and_report_preserves_state(self):
        value = probe(GREEN_PROBE)
        self.assertEqual(value['saved_after'], value['saved_before'])
        self.assertEqual(value['work_after'], value['work_before'])
        self.assertEqual(value['stash_after'], value['stash_before'])
        self.assertEqual(value['status_next'], value['log_next'])
        self.assertEqual(value['report_action'], value['run_action'])

    def test_ac6_generating_hint_does_not_create_stash(self):
        value = probe(GREEN_PROBE)
        self.assertEqual(value['stash_before_pause'], value['stash_after_pause'])
        self.assertEqual(value['stash_after_pause'], value['stash_before'])
        self.assertIn('由你决定取回或丢弃暂存的改动', value['run_next'])
        self.assertIn('由你决定取回或丢弃暂存的改动', value['report_action'])


if __name__ == '__main__':
    unittest.main()
