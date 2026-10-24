"""Committed memory landings survive failures and resume without rewriting history."""

from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from storyloop import lesson_landing
from storyloop.drafts import create_draft, load_draft, set_draft_status
from storyloop.lesson_landing import approve_draft
from storyloop.models import StoryLoopError
from storyloop.storage import load_json, write_json
from tests.lesson_support import cleanup_repo, commit_file, head, initialized_project, sample, _git


class LandingRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.project = initialized_project()
        self.addCleanup(cleanup_repo, self.project)

    def draft(self, landing=None, source='manual', **overrides):
        entry = sample(**overrides)
        entry.pop('id')
        body = entry.pop('body')
        payload = dict(action='create', target=None, entry=entry,
                       why_needed='需要', evidence=['人工确认'], landing=landing)
        return 'lesson/' + create_draft(self.project, 'lesson', source, None, payload, body)

    def pending(self, ref):
        return self.project / '.storyloop/state/memory-landings' / (ref + '.json')

    def fail_after_commit(self, ref):
        def fail(project, reference, status, failure=None):
            if status == 'approved':
                raise OSError('approval state unavailable')
            set_draft_status(project, reference, status, failure)
        with mock.patch.object(lesson_landing, 'set_draft_status', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'approval state unavailable'):
                approve_draft(self.project, ref)

    def test_postcommit_failure_preserves_commit_and_retry_finishes_same_lesson(self):
        ref = self.draft()
        parent = head(self.project)
        self.fail_after_commit(ref)
        committed = head(self.project)
        self.assertNotEqual(committed, parent)
        self.assertEqual(_git(self.project, 'rev-parse', 'HEAD^'), parent)
        self.assertEqual(_git(self.project, 'status', '--porcelain'), '')
        self.assertEqual(load_draft(self.project, ref)['status'], 'pending')
        result = approve_draft(self.project, ref)
        self.assertEqual(result['lesson_id'], 'L-0001')
        self.assertEqual(result['commit'], committed)
        self.assertEqual(head(self.project), committed)
        self.assertEqual(load_draft(self.project, ref)['status'], 'approved')
        self.assertFalse((self.project / 'openspec/lessons/L-0002.md').exists())

    def test_crash_after_commit_resumes_in_a_fresh_interpreter_without_another_commit(self):
        ref = self.draft()
        with mock.patch.object(lesson_landing, 'set_draft_status', side_effect=SystemExit('interrupted')):
            with self.assertRaises(SystemExit):
                approve_draft(self.project, ref)
        committed = head(self.project)
        source = str(Path(lesson_landing.__file__).resolve().parents[1])
        script = (f'import sys; sys.path.insert(0, {source!r}); '
                  'from pathlib import Path; from storyloop.lesson_landing import approve_draft; '
                  f'result=approve_draft(Path({str(self.project)!r}), {ref!r}); '
                  'print(result["lesson_id"]); print(result["commit"])')
        result = subprocess.run([sys.executable, '-I', '-c', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['L-0001', committed])
        self.assertEqual(head(self.project), committed)

    def test_recovery_refuses_an_external_head_advance_and_preserves_every_commit(self):
        ref = self.draft()
        self.fail_after_commit(ref)
        committed = head(self.project)
        external = commit_file(self.project, 'external.txt', 'external work')
        with self.assertRaisesRegex(StoryLoopError, 'HEAD|提交'):
            approve_draft(self.project, ref)
        self.assertEqual(head(self.project), external)
        self.assertEqual(_git(self.project, 'rev-parse', 'HEAD^'), committed)
        self.assertEqual((self.project / 'external.txt').read_text(), 'external work')

    def test_failure_never_invokes_reference_rewind(self):
        ref = self.draft()
        real_git = lesson_landing._git
        commands = []
        def observe(project, *args):
            commands.append(args)
            return real_git(project, *args)
        with mock.patch.object(lesson_landing, '_git', side_effect=observe):
            self.fail_after_commit(ref)
        self.assertFalse(any(args and args[0] == 'update-ref' for args in commands), commands)

    def test_recovery_refuses_a_result_id_that_does_not_match_the_commit(self):
        ref = self.draft()
        self.fail_after_commit(ref)
        committed = head(self.project)
        record = load_json(self.pending(ref))
        record['result_ids']['lesson_id'] = 'L-9999'
        write_json(self.pending(ref), record)
        with self.assertRaisesRegex(StoryLoopError, '标识|路径'):
            approve_draft(self.project, ref)
        self.assertEqual(head(self.project), committed)
        self.assertEqual(load_draft(self.project, ref)['status'], 'pending')
        self.assertTrue(self.pending(ref).is_file())

    def test_recovery_refuses_missing_check_configuration(self):
        source = 'FEAT-20261022-001'
        commit_file(self.project, 'app/__init__.py', '')
        red = commit_file(self.project, 'app/clock.py', 'SYNCED = False\n')
        commit_file(self.project, 'app/clock.py', 'SYNCED = True\n')
        write_json(self.project / f'.storyloop/state/runs/{source}.json',
                   {'status': 'PASSED', 'contract_commit': red})
        argv = [sys.executable, '-c', 'import app.clock, sys; sys.exit(not app.clock.SYNCED)']
        ref = self.draft(source=source, strength='check', landing={'kind': 'check_command', 'argv': argv})
        with mock.patch.object(lesson_landing, '_land_check_config', side_effect=OSError('config unavailable')):
            with self.assertRaisesRegex(OSError, 'config unavailable'):
                approve_draft(self.project, ref)
        record = load_json(self.pending(ref))
        record['check_argv'] = None
        write_json(self.pending(ref), record)
        with self.assertRaisesRegex(StoryLoopError, '配置'):
            approve_draft(self.project, ref)
        self.assertNotIn(argv, load_json(self.project / '.storyloop/config.json')['profile'].get('check_commands', []))
        self.assertEqual(load_draft(self.project, ref)['status'], 'pending')

    def test_recovery_validates_proposal_before_changing_approval_or_removing_record(self):
        ref = self.draft(strength='knowledge', landing={
            'kind': 'foundation', 'path': 'openspec/foundation/runtime.md', 'append': '校准时钟。'})
        approve_draft(self.project, ref)
        committed = head(self.project)
        pending = lesson_landing._base_landing_path(self.project, ref)
        record = load_json(pending)
        record['result']['base_proposal'] = 'BASE-invalid'
        write_json(pending, record)
        with self.assertRaisesRegex(StoryLoopError, '提案'):
            approve_draft(self.project, ref)
        self.assertEqual(head(self.project), committed)
        self.assertEqual(load_draft(self.project, ref)['status'], 'pending')
        self.assertTrue(pending.is_file())

    def test_recovery_refuses_a_deleted_base_proposal(self):
        ref = self.draft(strength='knowledge', landing={
            'kind': 'foundation', 'path': 'openspec/foundation/runtime.md', 'append': '校准时钟。'})
        result = approve_draft(self.project, ref)
        pending = lesson_landing._base_landing_path(self.project, ref)
        (self.project / '.storyloop/base-proposals' / f"{result['base_proposal']}.json").unlink()
        with self.assertRaisesRegex(StoryLoopError, '提案'):
            approve_draft(self.project, ref)
        self.assertEqual(load_draft(self.project, ref)['status'], 'pending')
        self.assertTrue(pending.is_file())

    def test_recovery_refuses_an_edited_draft(self):
        ref = self.draft()
        self.fail_after_commit(ref)
        committed = head(self.project)
        body = self.project / '.storyloop/drafts' / ref / 'body.md'
        body.write_text('审批失败后另行修改的正文。', encoding='utf-8')
        with self.assertRaisesRegex(StoryLoopError, '草案已变化'):
            approve_draft(self.project, ref)
        self.assertEqual(head(self.project), committed)
        self.assertTrue(self.pending(ref).is_file())

    def test_recovery_refuses_another_branch_even_at_the_same_commit(self):
        ref = self.draft()
        self.fail_after_commit(ref)
        committed = head(self.project)
        _git(self.project, 'switch', '-c', 'recovery-other')
        with self.assertRaisesRegex(StoryLoopError, '分支已变化'):
            approve_draft(self.project, ref)
        self.assertEqual(head(self.project), committed)
        self.assertEqual(_git(self.project, 'branch', '--show-current'), 'recovery-other')

    def test_recovery_refuses_staged_external_work_and_keeps_the_index(self):
        ref = self.draft()
        self.fail_after_commit(ref)
        committed = head(self.project)
        (self.project / 'external.txt').write_text('other work', encoding='utf-8')
        _git(self.project, 'add', 'external.txt')
        index = (self.project / '.git/index').read_bytes()
        with self.assertRaisesRegex(StoryLoopError, '工作区必须干净'):
            approve_draft(self.project, ref)
        self.assertEqual(head(self.project), committed)
        self.assertEqual((self.project / '.git/index').read_bytes(), index)
        self.assertEqual((self.project / 'external.txt').read_text(), 'other work')

    def test_another_draft_cannot_hide_an_unfinished_landing(self):
        ref = self.draft()
        self.fail_after_commit(ref)
        committed = head(self.project)
        other = self.draft()
        with self.assertRaisesRegex(StoryLoopError, '请先恢复原草案'):
            approve_draft(self.project, other)
        self.assertEqual(head(self.project), committed)
        self.assertEqual(load_draft(self.project, other)['status'], 'pending')

    def test_crash_immediately_after_git_commit_resumes_the_recorded_transaction(self):
        ref = self.draft()
        actual = lesson_landing.commit_paths
        def crash(*args, **kwargs):
            actual(*args, **kwargs)
            raise SystemExit('crash before commit result')
        with mock.patch.object(lesson_landing, 'commit_paths', side_effect=crash):
            with self.assertRaises(SystemExit):
                approve_draft(self.project, ref)
        committed = head(self.project)
        self.assertEqual(approve_draft(self.project, ref)['commit'], committed)
        self.assertEqual(load_draft(self.project, ref)['status'], 'approved')

    def test_crash_after_approval_before_cleanup_finishes_without_reapplying(self):
        ref = self.draft()
        actual = lesson_landing.set_draft_status
        def crash(project, reference, status, failure=None):
            actual(project, reference, status, failure)
            if status == 'approved':
                raise SystemExit('crash after approval')
        with mock.patch.object(lesson_landing, 'set_draft_status', side_effect=crash):
            with self.assertRaises(SystemExit):
                approve_draft(self.project, ref)
        committed = head(self.project)
        self.assertEqual(load_draft(self.project, ref)['status'], 'approved')
        self.assertTrue(self.pending(ref).is_file())
        self.assertEqual(approve_draft(self.project, ref)['commit'], committed)
        self.assertFalse(self.pending(ref).exists())

    def test_draft_edit_during_candidate_preparation_prevents_commit(self):
        ref = self.draft()
        parent = head(self.project)
        actual = lesson_landing._candidate
        body = self.project / '.storyloop/drafts' / ref / 'body.md'
        def edit(*args):
            result = actual(*args)
            body.write_text('外部改过的正文。', encoding='utf-8')
            return result
        with mock.patch.object(lesson_landing, '_candidate', side_effect=edit):
            with self.assertRaisesRegex(StoryLoopError, '草案已变化'):
                approve_draft(self.project, ref)
        self.assertEqual(head(self.project), parent)
        self.assertEqual(_git(self.project, 'status', '--porcelain'), '')
        self.assertEqual(body.read_text(encoding='utf-8'), '外部改过的正文。')

    def test_first_approval_also_checks_branch_after_commit(self):
        ref = self.draft()
        actual = lesson_landing.commit_paths
        def switch(*args, **kwargs):
            result = actual(*args, **kwargs)
            _git(self.project, 'switch', '-c', 'external-branch')
            return result
        with mock.patch.object(lesson_landing, 'commit_paths', side_effect=switch):
            with self.assertRaisesRegex(StoryLoopError, '分支已变化'):
                approve_draft(self.project, ref)
        self.assertEqual(load_draft(self.project, ref)['status'], 'pending')
        self.assertTrue(self.pending(ref).is_file())
        self.assertEqual(_git(self.project, 'branch', '--show-current'), 'external-branch')

    def test_first_approval_also_checks_draft_after_commit(self):
        ref = self.draft()
        actual = lesson_landing.commit_paths
        def edit(*args, **kwargs):
            result = actual(*args, **kwargs)
            body = self.project / '.storyloop/drafts' / ref / 'body.md'
            body.write_text('提交后外部修改。', encoding='utf-8')
            return result
        with mock.patch.object(lesson_landing, 'commit_paths', side_effect=edit):
            with self.assertRaisesRegex(StoryLoopError, '草案已变化'):
                approve_draft(self.project, ref)
        self.assertEqual(load_draft(self.project, ref)['status'], 'pending')
        self.assertTrue(self.pending(ref).is_file())

    def test_first_approval_also_checks_base_proposal_after_commit(self):
        ref = self.draft(strength='knowledge', landing={
            'kind': 'foundation', 'path': 'openspec/foundation/runtime.md', 'append': '校准时钟。'})
        actual = lesson_landing.write_json
        def remove(path, value, **kwargs):
            actual(path, value, **kwargs)
            if 'base-landings' in str(path):
                for proposal in (self.project / '.storyloop/base-proposals').glob('*.json'):
                    proposal.unlink()
        with mock.patch.object(lesson_landing, 'write_json', side_effect=remove):
            with self.assertRaisesRegex(StoryLoopError, '提案'):
                approve_draft(self.project, ref)
        self.assertEqual(load_draft(self.project, ref)['status'], 'pending')
        self.assertFalse(lesson_landing._base_landing_path(self.project, ref).is_file())
