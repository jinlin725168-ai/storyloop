import shutil
import sys
import unittest
from unittest import mock

from storyloop.base_upgrade import upgrade_baseline
from storyloop.drafts import create_draft, load_draft, set_draft_status
from storyloop import lesson_landing
from storyloop.lesson_landing import approve_draft, prove_check
from storyloop.lesson_store import load_lessons_at
from storyloop.models import StoryLoopError
from storyloop.storage import load_json, write_json
from tests.lesson_support import initialized_project, sample, head, _git, commit_file


class LandingTests(unittest.TestCase):
    def setUp(self):
        self.project = initialized_project()
        self.addCleanup(shutil.rmtree, self.project)

    def draft(self, action='create', target=None, landing=None, source='manual', **overrides):
        entry = sample(**overrides)
        entry.pop('id')
        body = entry.pop('body')
        payload = dict(action=action, target=target, entry=entry, why_needed='需要',
                       evidence=['人工确认'], landing=landing)
        return 'lesson/' + create_draft(self.project, 'lesson', source, None, payload, body)

    def passed_change(self, bug_file='app/clock.py'):
        change_id = 'FEAT-20261019-007'
        commit_file(self.project, 'app/__init__.py', '')
        contract_commit = commit_file(self.project, bug_file, 'SYNCED = False\n')
        commit_file(self.project, bug_file, 'SYNCED = True\n')
        write_json(self.project / f'.storyloop/state/runs/{change_id}.json', {
            'status': 'PASSED', 'contract_commit': contract_commit,
        })
        return change_id

    def snapshot(self):
        p = self.project
        return (head(p), _git(p, 'status', '--porcelain'),
                (p / '.git/index').read_bytes(),
                (p / '.storyloop/config.json').read_bytes(),
                {x.name: x.read_bytes() for x in (p / '.storyloop/base-proposals').glob('*.json')})

    def foundation(self, path='openspec/foundation/runtime.md'):
        return dict(kind='foundation', path=path, append='## 时钟\n先校时。')

    def test_prose_create_and_consecutive_ids(self):
        for number in (1, 2):
            ref = self.draft()
            result = approve_draft(self.project, ref)
            self.assertEqual(result['lesson_id'], f'L-{number:04d}')
            self.assertEqual(result['commit'], head(self.project))
            self.assertEqual(load_draft(self.project, ref)['status'], 'approved')
            self.assertEqual(_git(self.project, 'log', '-1', '--format=%s'), 'chore(lessons): 经验')

    def test_update_and_retire(self):
        approve_draft(self.project, self.draft())
        approve_draft(self.project, self.draft('update', 'L-0001', summary='改过'))
        self.assertEqual(load_lessons_at(self.project, 'HEAD')[0]['summary'], '改过')
        approve_draft(self.project, self.draft('retire', 'L-0001'))
        self.assertEqual(load_lessons_at(self.project, 'HEAD')[0]['status'], 'retired')

    def test_knowledge_exact_paths_and_actual_base_update_after_prose(self):
        approve_draft(self.project, self.draft())
        result = approve_draft(self.project, self.draft(strength='knowledge', landing=self.foundation()))
        changed = _git(self.project, 'show', '--name-only', '--format=', 'HEAD').splitlines()
        self.assertEqual(sorted(changed), ['openspec/foundation/runtime.md', 'openspec/lessons/L-0002.md'])
        self.assertIn('base-update', result['next'])
        self.assertEqual(upgrade_baseline(self.project, result['base_proposal'])['status'], 'BASELINE_UPGRADED')

    def test_unicode_foundation_snapshot(self):
        approve_draft(self.project, self.draft(strength='knowledge', landing=self.foundation('openspec/foundation/运行时.md')))
        self.assertEqual(load_lessons_at(self.project, 'HEAD')[0]['strength'], 'knowledge')

    def test_config_refused(self):
        with self.assertRaises(StoryLoopError) as caught:
            approve_draft(self.project, self.draft(strength='config'))
        self.assertEqual(caught.exception.code, 'LANDING_UNSUPPORTED')

    def test_check_proven_red_then_green_lands_in_config(self):
        change_id = self.passed_change()
        argv = [sys.executable, '-c',
                'import app.clock, sys; sys.exit(0 if app.clock.SYNCED else 1)']
        result = approve_draft(self.project, self.draft(
            source=change_id, strength='check',
            landing={'kind': 'check_command', 'argv': argv},
        ))
        config = load_json(self.project / '.storyloop/config.json')
        self.assertIn(argv, config['profile']['check_commands'])
        self.assertIsNone(result['base_proposal'])

    def test_check_that_is_green_on_contract_commit_is_rejected(self):
        change_id = self.passed_change()
        before = self.snapshot()
        with self.assertRaises(StoryLoopError) as caught:
            approve_draft(self.project, self.draft(
                source=change_id, strength='check',
                landing={'kind': 'check_command',
                         'argv': [sys.executable, '-c', 'pass']},
            ))
        self.assertEqual(caught.exception.code, 'CHECK_NOT_PROVEN')
        self.assertEqual(self.snapshot(), before)
        worktrees = _git(self.project, 'worktree', 'list', '--porcelain')
        self.assertEqual(worktrees.count('worktree '), 1)

    def test_manual_source_cannot_prove_red(self):
        landing = {'kind': 'check_command', 'argv': [sys.executable, '-c', 'pass']}
        with self.assertRaisesRegex(StoryLoopError, '证明会红'):
            approve_draft(self.project, self.draft(strength='check', landing=landing))

    def test_worktree_registered_before_add_failure_is_removed(self):
        real_git = lesson_landing._git
        registered = []

        def fail_after_registration(project, *args):
            if args[:2] == ('worktree', 'add'):
                checkout, commit = args[-2:]
                real_git(project, 'worktree', 'add', '--detach', '--no-checkout',
                         checkout, commit)
                registered.append(checkout)
                raise StoryLoopError('checkout failed')
            return real_git(project, *args)

        try:
            with mock.patch('storyloop.lesson_landing._git', side_effect=fail_after_registration):
                with self.assertRaisesRegex(StoryLoopError, 'checkout failed'):
                    lesson_landing._run_check_at(
                        self.project, [sys.executable, '-c', 'pass'], head(self.project), 10,
                    )
            listed = real_git(self.project, 'worktree', 'list', '--porcelain')
            self.assertNotIn(registered[0], listed)
        finally:
            for checkout in registered:
                if checkout in real_git(self.project, 'worktree', 'list', '--porcelain'):
                    real_git(self.project, 'worktree', 'remove', '--force', checkout)

    def test_process_failures_cannot_count_as_red(self):
        good = {'returncode': 0, 'contained': True, 'timed_out': False,
                'truncated': False, 'error': ''}
        cases = (
            {'returncode': 1, 'contained': True, 'timed_out': True,
             'truncated': False, 'error': ''},
            {'returncode': None, 'contained': False, 'timed_out': False,
             'truncated': False, 'error': 'Unable to start configured executable'},
            {'returncode': 1, 'contained': False, 'timed_out': False,
             'truncated': False, 'error': ''},
            {'returncode': 1, 'contained': True, 'timed_out': False,
             'truncated': True, 'error': ''},
        )
        for invalid in cases:
            with self.subTest(result=invalid):
                with mock.patch('storyloop.lesson_landing._run_check_at',
                                side_effect=[invalid, good]):
                    with self.assertRaises(StoryLoopError) as caught:
                        prove_check(self.project, [sys.executable, '-c', 'pass'],
                                    head(self.project), head(self.project))
                self.assertEqual(caught.exception.code, 'CHECK_NOT_PROVEN')

    def test_check_config_write_failure_restores_commit_config_and_index(self):
        change_id = self.passed_change()
        argv = [sys.executable, '-c',
                'import app.clock, sys; sys.exit(0 if app.clock.SYNCED else 1)']
        ref = self.draft(source=change_id, strength='check',
                         landing={'kind': 'check_command', 'argv': argv})
        before = self.snapshot()

        def write_then_fail(path, value, max_bytes=None):
            write_json(path, value, max_bytes=max_bytes)
            raise OSError('config write failure')

        with mock.patch('storyloop.lesson_landing.write_json', side_effect=write_then_fail):
            with self.assertRaisesRegex(OSError, 'config write failure'):
                approve_draft(self.project, ref)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(load_draft(self.project, ref)['status'], 'pending')

    def test_invalid_draft_no_trace(self):
        ref = self.draft(tier='legendary')
        before = self.snapshot()
        with self.assertRaisesRegex(StoryLoopError, 'tier'):
            approve_draft(self.project, ref)
        self.assertEqual(self.snapshot(), before)
        self.assertIn('tier', load_draft(self.project, ref)['failure'])

    def test_commit_failure_restores_exact_snapshot(self):
        ref = self.draft(strength='knowledge', landing=self.foundation())
        before = self.snapshot()
        with mock.patch('storyloop.lesson_landing.commit_paths', side_effect=StoryLoopError('boom')):
            with self.assertRaisesRegex(StoryLoopError, 'boom'):
                approve_draft(self.project, ref)
        self.assertEqual(self.snapshot(), before)

    def test_postcommit_failure_restores_exact_snapshot(self):
        ref = self.draft(strength='knowledge', landing=self.foundation())
        before = self.snapshot()
        def fail_approval(project, reference, status, failure=None):
            if status == 'approved':
                raise StoryLoopError('postcommit')
            set_draft_status(project, reference, status, failure)
        with mock.patch('storyloop.lesson_landing.set_draft_status', side_effect=fail_approval):
            with self.assertRaisesRegex(StoryLoopError, 'postcommit'):
                approve_draft(self.project, ref)
        self.assertEqual(self.snapshot(), before)

    def test_external_commit_is_preserved(self):
        ref = self.draft()
        external = []
        def fail_approval(project, reference, status, failure=None):
            if status == 'approved':
                external.append(commit_file(project, 'external.txt', 'other work'))
                raise StoryLoopError('external')
            set_draft_status(project, reference, status, failure)
        with mock.patch('storyloop.lesson_landing.set_draft_status', side_effect=fail_approval):
            with self.assertRaises(StoryLoopError):
                approve_draft(self.project, ref)
        self.assertEqual(head(self.project), external[0])
        self.assertEqual(_git(self.project, 'status', '--porcelain'), '')

    def test_active_run_refused(self):
        write_json(self.project / '.storyloop/state/runs/FEAT-20261019-009.json', {'status': 'IMPLEMENTING'})
        with self.assertRaisesRegex(StoryLoopError, 'FEAT-20261019-009'):
            approve_draft(self.project, self.draft())

    def test_postcommit_query_failure_restores_snapshot(self):
        from storyloop import project_git
        real_git = project_git._git
        def fail_query(project, *args):
            if args == ('rev-parse', 'HEAD'):
                raise StoryLoopError('result query failed')
            return real_git(project, *args)
        ref = self.draft()
        before = self.snapshot()
        with mock.patch('storyloop.project_git._git', side_effect=fail_query):
            with self.assertRaisesRegex(StoryLoopError, 'result query failed'):
                approve_draft(self.project, ref)
        self.assertEqual(self.snapshot(), before)

    def test_cli_draft_dispatch_and_exclusion(self):
        import json
        from tests.test_cli import storyloop
        ref = self.draft()
        result = storyloop('approve', str(self.project), '--draft', ref, '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'APPLIED')
        result = storyloop('approve', str(self.project), '--draft', ref, '--change', 'FEAT-1')
        self.assertNotEqual(result.returncode, 0)

    def test_unknown_role_and_core_budget_rejected(self):
        ref = self.draft(roles=['unknown'])
        with self.assertRaises(StoryLoopError):
            approve_draft(self.project, ref)
        approve_draft(self.project, self.draft(tier='core', body='x' * 3000))
        before = self.snapshot()
        with self.assertRaises(StoryLoopError):
            approve_draft(self.project, self.draft(tier='core', body='y' * 2000))
        self.assertEqual(self.snapshot(), before)

    def test_existing_foundation_bytes_restored(self):
        commit_file(self.project, 'openspec/foundation/runtime.md', 'original\r\n')
        # Bind this foundation as baseline before injecting a failed landing.
        from storyloop.base_upgrade import propose_base_change
        proposal = propose_base_change(self.project, ['openspec/foundation/runtime.md'], 'seed')
        upgrade_baseline(self.project, proposal['change_id'])
        before = (self.project / 'openspec/foundation/runtime.md').read_bytes()
        ref = self.draft(strength='knowledge', landing=self.foundation())
        with mock.patch('storyloop.lesson_landing.commit_paths', side_effect=StoryLoopError('boom')):
            with self.assertRaises(StoryLoopError):
                approve_draft(self.project, ref)
        self.assertEqual((self.project / 'openspec/foundation/runtime.md').read_bytes(), before)

    def test_invalid_missing_landing_reference_leaves_no_trace(self):
        ref = self.draft(landings=[{'kind': 'foundation', 'path': 'openspec/foundation/missing.md'}])
        before = self.snapshot()
        with self.assertRaises(StoryLoopError):
            approve_draft(self.project, ref)
        self.assertEqual(self.snapshot(), before)

    def test_write_failure_after_proposal_restores_snapshot(self):
        from storyloop.lesson_landing import write_bytes_atomic
        failed = []
        def fail_once(path, content):
            if 'openspec/foundation' in str(path) and not failed:
                failed.append(True)
                raise OSError('write failure')
            return write_bytes_atomic(path, content)
        ref = self.draft(strength='knowledge', landing=self.foundation())
        before = self.snapshot()
        with mock.patch('storyloop.lesson_landing.write_bytes_atomic', side_effect=fail_once):
            with self.assertRaises(OSError):
                approve_draft(self.project, ref)
        self.assertEqual(self.snapshot(), before)

    def test_trailing_summary_whitespace_approves_once(self):
        for suffix in (' ', '\t'):
            with self.subTest(suffix=repr(suffix)):
                self.project = initialized_project()
                self.addCleanup(shutil.rmtree, self.project)
                ref = self.draft(summary='经验' + suffix)
                result = approve_draft(self.project, ref)
                self.assertEqual(result['commit'], head(self.project))
                self.assertEqual(load_draft(self.project, ref)['status'], 'approved')
                self.assertEqual(_git(self.project, 'log', '-1', '--format=%s'), 'chore(lessons): 经验')
                before = head(self.project)
                with self.assertRaisesRegex(StoryLoopError, 'pending'):
                    approve_draft(self.project, ref)
                self.assertEqual(head(self.project), before)

    def test_trailing_summary_whitespace_postcommit_failure_rolls_back(self):
        def fail_approval(project, reference, status, failure=None):
            if status == 'approved':
                raise StoryLoopError('postcommit whitespace')
            set_draft_status(project, reference, status, failure)
        for suffix in (' ', '\t'):
            with self.subTest(suffix=repr(suffix)):
                self.project = initialized_project()
                self.addCleanup(shutil.rmtree, self.project)
                ref = self.draft(summary='经验' + suffix, strength='knowledge', landing=self.foundation())
                before = self.snapshot()
                with mock.patch('storyloop.lesson_landing.set_draft_status', side_effect=fail_approval):
                    with self.assertRaisesRegex(StoryLoopError, 'postcommit whitespace'):
                        approve_draft(self.project, ref)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(load_draft(self.project, ref)['status'], 'pending')

    def test_unicode_summary_separator_preserves_commit_identity(self):
        summary = '经验\u2028继续'
        ref = self.draft(summary=summary)
        result = approve_draft(self.project, ref)
        self.assertEqual(result['commit'], head(self.project))
        self.assertEqual(load_draft(self.project, ref)['status'], 'approved')
        self.assertEqual(load_lessons_at(self.project, 'HEAD')[0]['summary'], summary)

    def test_unicode_summary_separator_postcommit_failure_rolls_back(self):
        ref = self.draft(summary='经验\u2028继续')
        before = self.snapshot()
        def fail_approval(project, reference, status, failure=None):
            if status == 'approved':
                raise StoryLoopError('postcommit separator')
            set_draft_status(project, reference, status, failure)
        with mock.patch('storyloop.lesson_landing.set_draft_status', side_effect=fail_approval):
            with self.assertRaisesRegex(StoryLoopError, 'postcommit separator'):
                approve_draft(self.project, ref)
        self.assertEqual(self.snapshot(), before)
