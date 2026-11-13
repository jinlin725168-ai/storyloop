"""BASE_CHANGE_REQUIRED 的独立 API 验收用例。"""
import json
import shlex
import subprocess
import sys
import unittest
from pathlib import Path

from storyloop.testing.smoke_harness.v1 import HarnessError


PROBE = r'''
import json
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from storyloop.baseline import bootstrap_project
from storyloop.run_hints import next_step
from storyloop.run_stops import pause
from storyloop.stop_report import stop_of

mode = sys.argv[1]
change_id = 'FEAT-1'
paths = ['ordinary.py', '[ab].py', ':(exclude)other.py']
all_paths = paths + ['a.py', 'b.py', 'unlisted.py']

def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                            text=True, timeout=10)
    if result.returncode:
        raise RuntimeError('Git fixture failed: ' + result.stderr)
    return result.stdout

def commands(text):
    return [match.group() for match in re.finditer(
        r'git .*?(?= 保存| 检查|；|，|$)', text)]

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
    for name in all_paths:
        (root / name).write_text('before:' + name + '\n', encoding='utf-8')
    git(root, '--literal-pathspecs', 'add', '--', '.gitignore', *all_paths)
    git(root, 'commit', '-qm', 'fixture')
    for name in all_paths:
        (root / name).write_text('after:' + name + '\n', encoding='utf-8')
    legacy = [] if mode == 'empty' else paths
    details = {'blocked_by': 'BASE-1', 'legacy_paths': legacy,
               'source_branch': 'main', 'feature_branch': 'feature/FEAT-1'}
    state = {'change_id': change_id, 'status': 'IMPLEMENTING', 'attempt': 1,
             'failures': []}
    before_pause_stash = git(root, 'stash', 'list')
    run = pause(root, state, 'BASE_CHANGE_REQUIRED', **details)
    after_pause_stash = git(root, 'stash', 'list')
    product = {'version': 1, 'goal': 'Reserve a meeting room without overlap',
               'non_goals': [], 'questions': [], 'stories': [{
                   'id': 'ST-1', 'title': 'Reserve a room', 'as_a': 'employee',
                   'want': 'reserve a free room', 'so_that': 'meetings do not collide',
                   'rules': ['No overlap'], 'out_of_scope': [], 'depends_on': [],
                   'acceptance': [{'id': 'AC-1', 'given': 'a reservation exists',
                                   'when': 'another overlaps', 'then': 'reject it',
                                   'kind': 'automated'}]}]}
    change = {'version': 1, 'change_id': change_id, 'status': 'APPROVED',
              'revision': 1, 'idea': 'Reserve a room', 'product': product,
              'created_at': '2026-01-01T00:00:00Z',
              'updated_at': '2026-01-01T00:00:00Z', 'history': [],
              'approval': None}
    change_path = root / '.storyloop/state/changes' / (change_id + '.json')
    change_path.parent.mkdir(parents=True, exist_ok=True)
    change_path.write_text(json.dumps(change), encoding='utf-8')
    run_path = root / '.storyloop/state/runs' / (change_id + '.json')
    if mode == 'old':
        saved = json.loads(run_path.read_text(encoding='utf-8'))
        saved['stop']['options'][0]['action'] = (
            '先用 git stash push -u -- ordinary.py 保存遗留业务改动；'
            '用 git status --short 检查并另行保存其他改动；'
            'git switch main；base 通过后 git switch feature/FEAT-1')
        run_path.write_text(json.dumps(saved), encoding='utf-8')
    report_path = root / 'reports/storyloop' / change_id / 'report.md'
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(stop_of(root, json.loads(run_path.read_text(encoding='utf-8'))
                            )['options'][0]['action'], encoding='utf-8')
    saved_before = run_path.read_bytes().hex()
    work_before = git(root, 'status', '--porcelain', '--untracked-files=all')
    stash_before = git(root, 'stash', 'list')
    status = public('status', root, change_id)
    log = public('log', root, change_id)
    report = public('report', root, change_id)
    saved_after = run_path.read_bytes().hex()
    work_after = git(root, 'status', '--porcelain', '--untracked-files=all')
    stash_after = git(root, 'stash', 'list')
    report_action = Path(report['report']).read_text(encoding='utf-8')
    result = {'root': str(root), 'paths': paths, 'run_next': run['next'],
              'run_action': run['stop']['options'][0]['action'],
              'status_next': status['next'],
              'status_action': status['stop']['options'][0]['action'],
              'log_next': log['next'], 'log_action': log['stop']['options'][0]['action'],
              'report_action': report_action,
              'approval': run['stop']['options'][0]['command'],
              'before_pause_stash': before_pause_stash,
              'after_pause_stash': after_pause_stash,
              'saved_before': saved_before, 'saved_after': saved_after,
              'work_before': work_before, 'work_after': work_after,
              'stash_before': stash_before, 'stash_after': stash_after}
    if mode == 'stash':
        segment = run['next'].split('先用 ', 1)[1].split(' 保存', 1)[0]
        tokens = shlex.split(segment)
        result['stash_tokens'] = tokens
        result['stash_prefix'] = shlex.join(['git', '-C', str(root)])
        result['unlisted_before'] = {
            name: (root / name).read_text(encoding='utf-8')
            for name in all_paths if name not in paths}
        outside = Path(folder)
        executed = subprocess.run(tokens, cwd=outside, capture_output=True,
                                  text=True, timeout=10)
        result['stash_exit'] = executed.returncode
        result['stash_files'] = git(root, 'diff', '--name-only', 'stash@{0}^1', 'stash@{0}').splitlines() \
            if executed.returncode == 0 else []
        result['listed_after'] = {
            name: (root / name).read_text(encoding='utf-8') for name in paths}
        result['unlisted_after'] = {
            name: (root / name).read_text(encoding='utf-8')
            for name in all_paths if name not in paths}
    print(json.dumps(result, ensure_ascii=False))
'''


def probe(mode):
    try:
        child = subprocess.run([sys.executable, '-c', PROBE, mode], cwd=Path.cwd(),
                               capture_output=True, text=True, timeout=30)
        if child.returncode:
            raise HarnessError('Business subprocess failed: ' + child.stderr[-1500:])
        value = json.loads(child.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        raise HarnessError('Business subprocess/protocol unavailable') from error
    required = {'root', 'paths', 'run_next', 'run_action', 'status_next',
                'status_action', 'log_next', 'log_action', 'report_action',
                'approval', 'before_pause_stash', 'after_pause_stash',
                'saved_before', 'saved_after', 'work_before', 'work_after',
                'stash_before', 'stash_after'}
    if not isinstance(value, dict) or not required <= value.keys():
        raise HarnessError('Business protocol is invalid')
    return value


def git_parts(text):
    import re
    return [match.group() for match in re.finditer(
        r'git .*?(?= 保存| 检查|；|，|$)', text)]


class NewBehavior(unittest.TestCase):
    def test_ac1_stash_is_repo_bound_and_literal(self):
        value = probe('stash')
        tokens = value['stash_tokens']
        self.assertEqual(tokens[:3], ['git', '-C', value['root']])
        self.assertIn('--literal-pathspecs', tokens)
        self.assertEqual(tokens[tokens.index('stash'):],
                         ['stash', 'push', '-u', '--', *value['paths']])
        self.assertEqual(value['stash_exit'], 0)
        self.assertCountEqual(value['stash_files'], value['paths'])
        self.assertEqual(value['unlisted_after'], value['unlisted_before'])
        self.assertEqual(value['listed_after'],
                         {name: 'before:' + name + '\n' for name in value['paths']})

    def test_ac2_every_view_repairs_saved_and_current_commands(self):
        for mode in ('views', 'old'):
            with self.subTest(mode=mode):
                value = probe(mode)
                prefix = shlex.join(['git', '-C', value['root']])
                expected = ['stash', 'status', 'switch', 'switch']
                surfaces = [value[key] for key in (
                    'run_next', 'run_action', 'status_next', 'status_action',
                    'log_next', 'log_action', 'report_action')]
                for surface in surfaces:
                    parts = git_parts(surface)
                    self.assertEqual(len(parts), 4, surface)
                    self.assertEqual([shlex.split(part)[3] if shlex.split(part)[3] != '--literal-pathspecs'
                                      else shlex.split(part)[4] for part in parts], expected)
                    self.assertTrue(all(part.startswith(prefix + ' ') for part in parts))
                    self.assertIn('--literal-pathspecs', parts[0])
                    self.assertEqual(shlex.split(parts[0])[-3:], value['paths'])
                self.assertEqual(git_parts(value['run_next']),
                                 git_parts(value['status_next']))
                self.assertEqual(git_parts(value['run_next']),
                                 git_parts(value['log_next']))

    def test_ac3_without_legacy_files_switches_stay_repo_bound(self):
        value = probe('empty')
        parts = git_parts(value['run_next'])
        prefix = shlex.join(['git', '-C', value['root']])
        self.assertEqual(len(parts), 2)
        self.assertTrue(all(part.startswith(prefix + ' switch ') for part in parts))
        self.assertNotIn('git stash', value['run_next'])
        self.assertEqual([shlex.split(part)[-1] for part in parts],
                         ['main', 'feature/FEAT-1'])


class ExistingBehavior(unittest.TestCase):
    def test_ac4_approval_and_action_order(self):
        value = probe('views')
        self.assertEqual(shlex.split(value['approval']),
                         ['storyloop', 'approve', value['root'], '--change', 'BASE-1'])
        for surface in (value['run_next'], value['run_action']):
            markers = ['stash', 'status', 'switch', 'storyloop approve',
                       'storyloop run', 'feature/FEAT-1', '取回或丢弃暂存']
            positions = [surface.index(marker) for marker in markers]
            self.assertEqual(positions, sorted(positions))

    def test_ac5_read_views_preserve_saved_and_git_state(self):
        value = probe('views')
        self.assertEqual(value['saved_after'], value['saved_before'])
        self.assertEqual(value['work_after'], value['work_before'])
        self.assertEqual(value['stash_after'], value['stash_before'])

    def test_ac6_hint_generation_does_not_stash(self):
        value = probe('views')
        self.assertEqual(value['before_pause_stash'], value['after_pause_stash'])
        self.assertEqual(value['after_pause_stash'], value['stash_after'])
        self.assertIn('由你决定取回或丢弃暂存', value['run_next'])


if __name__ == '__main__':
    unittest.main()
