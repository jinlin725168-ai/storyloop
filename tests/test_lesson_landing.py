import sys
from pathlib import Path
import unittest
from unittest import mock

from tests.lesson_support import cleanup_repo
from storyloop.base_upgrade import upgrade_baseline
from storyloop.drafts import create_draft, load_draft, set_draft_status
from storyloop import lesson_landing
from storyloop.lesson_landing import approve_draft, prove_check
from storyloop.lesson_store import load_lessons_at
from storyloop.models import StoryLoopError
from storyloop.storage import load_json, write_json
from tests.lesson_support import initialized_project, git_repo, sample, head, _git, commit_file


class LandingTests(unittest.TestCase):
    def setUp(self):
        self.project = initialized_project()
        self.addCleanup(cleanup_repo, self.project)

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

    def test_base_proposal_paths_detect_renamed_foundation_source(self):
        from storyloop.baseline import bootstrap_project
        project = git_repo(files={"README.md": "repo\n",
                                  "openspec/foundation/x.md": "same content\n"})
        self.addCleanup(cleanup_repo, project)
        bootstrap_project(project, {"test_commands": [[sys.executable, "-c", "pass"]]})
        _git(project, "config", "diff.renames", "true")
        (project / "openspec/lessons").mkdir(parents=True, exist_ok=True)
        _git(project, "mv", "openspec/foundation/x.md", "openspec/lessons/x.md")
        _git(project, "commit", "-q", "-m", "rename foundation into lessons")
        with self.assertRaisesRegex(StoryLoopError, "非经验变更"):
            lesson_landing._proposal_paths(project, {"openspec/lessons/L-0001.md": b"lesson"})

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

    def test_retire_check_preserves_config_and_landing_without_proof(self):
        from storyloop.lessons import render_lesson
        argv = [sys.executable, '-c', 'pass']
        landing = {'kind': 'check_command', 'argv': argv}
        commit_file(self.project, 'openspec/lessons/L-0001.md',
                    render_lesson(sample(strength='check', landings=[landing])))
        config_path = self.project / '.storyloop/config.json'
        config = load_json(config_path)
        config['profile']['check_commands'] = [argv]
        write_json(config_path, config)
        before = config_path.read_bytes()
        with mock.patch('storyloop.lesson_landing.prove_check') as proof:
            result = approve_draft(self.project, self.draft('retire', 'L-0001'))
        proof.assert_not_called()
        self.assertEqual(config_path.read_bytes(), before)
        entry = load_lessons_at(self.project, 'HEAD')[0]
        self.assertEqual(entry['status'], 'retired')
        self.assertEqual(entry['landings'], [landing])
        self.assertIsNone(result['base_proposal'])
        self.assertEqual(_git(self.project, 'show', '--format=', '--name-only', 'HEAD'),
                         'openspec/lessons/L-0001.md')

    def test_retire_and_update_config_preserve_actual_configuration(self):
        from storyloop.lessons import render_lesson
        landing = {'kind': 'check_command', 'argv': [sys.executable, '-c', 'pass']}
        commit_file(self.project, 'openspec/lessons/L-0001.md',
                    render_lesson(sample(strength='config', landings=[landing])))
        config = (self.project / '.storyloop/config.json').read_bytes()
        for action in ('update', 'retire'):
            with self.subTest(action=action):
                result = approve_draft(self.project, self.draft(
                    action, 'L-0001', strength='config', landings=[landing], summary='修订说明'))
                self.assertEqual((self.project / '.storyloop/config.json').read_bytes(), config)
                entry = load_lessons_at(self.project, 'HEAD')[0]
                self.assertEqual(entry['strength'], 'config')
                self.assertEqual(entry['status'], 'retired' if action == 'retire' else 'active')
                self.assertIsNone(result['base_proposal'])
                self.assertEqual(_git(self.project, 'show', '--format=', '--name-only', 'HEAD'),
                                 'openspec/lessons/L-0001.md')

    def test_update_cannot_transition_to_config(self):
        approve_draft(self.project, self.draft())
        before = self.snapshot()
        with self.assertRaises(StoryLoopError) as caught:
            approve_draft(self.project, self.draft('update', 'L-0001', strength='config'))
        self.assertEqual(caught.exception.code, 'LANDING_UNSUPPORTED')
        self.assertEqual(self.snapshot(), before)

    def test_knowledge_exact_paths_and_actual_base_update_after_prose(self):
        approve_draft(self.project, self.draft())
        source_head = head(self.project)
        result = approve_draft(self.project, self.draft(strength='knowledge', landing=self.foundation()))
        self.assertEqual(head(self.project), source_head)
        self.assertEqual(result['status'], 'BASE_APPROVAL_REQUIRED')
        self.assertEqual(sorted(result['paths']),
                         ['openspec/foundation/runtime.md', 'openspec/lessons/L-0002.md'])
        self.assertIn('storyloop approve', result['next'])
        self.assertEqual(upgrade_baseline(self.project, result['base_proposal'])['status'], 'APPROVAL_REQUIRED')

    def test_unicode_foundation_snapshot(self):
        approve_draft(self.project, self.draft(strength='knowledge', landing=self.foundation('openspec/foundation/运行时.md')))
        self.assertEqual(load_lessons_at(self.project, 'HEAD'), [])
        self.assertTrue((self.project / 'openspec/foundation/运行时.md').is_file())

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

    def test_check_config_write_failure_preserves_commit_and_retry_deduplicates_config(self):
        change_id = self.passed_change()
        argv = [sys.executable, '-c',
                'import app.clock, sys; sys.exit(0 if app.clock.SYNCED else 1)']
        ref = self.draft(source=change_id, strength='check',
                         landing={'kind': 'check_command', 'argv': argv})
        before = self.snapshot()

        def write_then_fail(path, value, max_bytes=None):
            write_json(path, value, max_bytes=max_bytes)
            if path == self.project / '.storyloop/config.json':
                raise OSError('config write failure')

        with mock.patch('storyloop.lesson_landing.write_json', side_effect=write_then_fail):
            with self.assertRaisesRegex(OSError, 'config write failure'):
                approve_draft(self.project, ref)
        committed = head(self.project)
        self.assertNotEqual(committed, before[0])
        self.assertEqual(_git(self.project, 'status', '--porcelain'), '')
        self.assertEqual(load_draft(self.project, ref)['status'], 'pending')
        result = approve_draft(self.project, ref)
        self.assertEqual(result['commit'], committed)
        self.assertEqual(result['lesson_id'], 'L-0001')
        self.assertEqual(load_json(self.project / '.storyloop/config.json')['profile']['check_commands'].count(argv), 1)

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
        with mock.patch('storyloop.lesson_landing.write_json', side_effect=StoryLoopError('boom')):
            with self.assertRaisesRegex(StoryLoopError, 'boom'):
                approve_draft(self.project, ref)
        self.assertEqual(self.snapshot(), before)

    def test_postcommit_failure_preserves_knowledge_and_reuses_base_proposal(self):
        ref = self.draft(strength='knowledge', landing=self.foundation())
        before = self.snapshot()
        first = approve_draft(self.project, ref)
        self.assertEqual(head(self.project), before[0])
        proposals = list((self.project / '.storyloop/base-proposals').glob('*.json'))
        self.assertEqual(len(proposals), 1)
        self.assertTrue((self.project / 'openspec/foundation/runtime.md').is_file())
        result = approve_draft(self.project, ref)
        self.assertEqual(result, first)
        self.assertEqual(result['base_proposal'], proposals[0].stem)
        self.assertEqual(list((self.project / '.storyloop/base-proposals').glob('*.json')), proposals)
        self.assertEqual(upgrade_baseline(self.project, result['base_proposal'])['status'], 'APPROVAL_REQUIRED')

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

    def test_postcommit_query_failure_preserves_commit_and_resumes(self):
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
        committed = head(self.project)
        self.assertNotEqual(committed, before[0])
        self.assertEqual(approve_draft(self.project, ref)['commit'], committed)

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
        with mock.patch('storyloop.lesson_landing.write_json', side_effect=StoryLoopError('boom')):
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
            if 'openspec/foundation' in Path(path).as_posix() and not failed:  # Windows paths use backslashes
                failed.append(True)
                raise OSError('write failure')
            return write_bytes_atomic(path, content)
        ref = self.draft(strength='knowledge', landing=self.foundation())
        before = self.snapshot()
        with mock.patch('storyloop.lesson_landing.write_bytes_atomic', side_effect=fail_once):
            with self.assertRaises(OSError):
                approve_draft(self.project, ref)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list((self.project / '.storyloop/state/changes').glob('BASE-*.json')), [])
        self.assertEqual(approve_draft(self.project, ref)['status'], 'BASE_APPROVAL_REQUIRED')

    def test_trailing_summary_whitespace_approves_once(self):
        for suffix in (' ', '\t'):
            with self.subTest(suffix=repr(suffix)):
                self.project = initialized_project()
                self.addCleanup(cleanup_repo, self.project)
                ref = self.draft(summary='经验' + suffix)
                result = approve_draft(self.project, ref)
                self.assertEqual(result['commit'], head(self.project))
                self.assertEqual(load_draft(self.project, ref)['status'], 'approved')
                self.assertEqual(_git(self.project, 'log', '-1', '--format=%s'), 'chore(lessons): 经验')
                before = head(self.project)
                with self.assertRaisesRegex(StoryLoopError, 'pending'):
                    approve_draft(self.project, ref)
                self.assertEqual(head(self.project), before)

    def test_trailing_summary_whitespace_postcommit_failure_recovers_same_commit(self):
        for suffix in (' ', '\t'):
            with self.subTest(suffix=repr(suffix)):
                self.project = initialized_project()
                self.addCleanup(cleanup_repo, self.project)
                ref = self.draft(summary='经验' + suffix, strength='knowledge', landing=self.foundation())
                before = self.snapshot()
                result = approve_draft(self.project, ref)
                self.assertEqual(head(self.project), before[0])
                self.assertEqual(load_draft(self.project, ref)['status'], 'pending')
                self.assertEqual(approve_draft(self.project, ref), result)

    def test_unicode_summary_separator_preserves_commit_identity(self):
        summary = '经验\u2028继续'
        ref = self.draft(summary=summary)
        result = approve_draft(self.project, ref)
        self.assertEqual(result['commit'], head(self.project))
        self.assertEqual(load_draft(self.project, ref)['status'], 'approved')
        self.assertEqual(load_lessons_at(self.project, 'HEAD')[0]['summary'], summary)

    def test_unicode_summary_separator_postcommit_failure_recovers_same_commit(self):
        ref = self.draft(summary='经验\u2028继续')
        before = self.snapshot()
        def fail_approval(project, reference, status, failure=None):
            if status == 'approved':
                raise StoryLoopError('postcommit separator')
            set_draft_status(project, reference, status, failure)
        with mock.patch('storyloop.lesson_landing.set_draft_status', side_effect=fail_approval):
            with self.assertRaisesRegex(StoryLoopError, 'postcommit separator'):
                approve_draft(self.project, ref)
        committed = head(self.project)
        self.assertNotEqual(committed, before[0])
        self.assertEqual(approve_draft(self.project, ref)['commit'], committed)
