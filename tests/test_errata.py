import json
import sys
import unittest
from unittest import mock

from tests.lesson_support import cleanup_repo
from storyloop.errata import capture_errata, next_errata_id, ERRATA_PATH, defer_errata, release_errata
from storyloop.drafts import load_draft
from storyloop.lesson_landing import approve_draft
from storyloop.base_upgrade import upgrade_baseline
from storyloop.baseline import bootstrap_project
from storyloop.foundation_survey import foundation_payload
from storyloop.models import StoryLoopError
from storyloop.project_agents import invoke_project_agent
from storyloop.project_loop_guards import read_only_call, write_safely
from storyloop.storage import load_json, write_json
from tests.lesson_support import git_repo, _git, head

ERRATUM = dict(document='openspec/foundation/runtime.md', claim='时钟同步。',
               correction='时钟尚未同步。', evidence=['app/clock.py:1'])


class ErrataTests(unittest.TestCase):
    def setUp(self):
        self.project = git_repo(files={'README.md': 'repo\n', 'app/clock.py': 'SYNCED = False\n',
            'openspec/foundation/README.md': '# foundation\n',
            ERRATUM['document']: '# 运行时\n时钟同步。\n'})
        self.addCleanup(cleanup_repo, self.project)
        bootstrap_project(self.project, {'test_commands': [[sys.executable, '-c', 'pass']]})

    def capture(self, **changes):
        return capture_errata(self.project, {'errata': [{**ERRATUM, **changes}]},
                              'implement', 'call-1', 'FEAT-1')

    def test_valid_erratum_becomes_draft(self):
        result = self.capture()
        self.assertEqual(len(result['drafts']), 1)
        draft = load_draft(self.project, 'errata/' + result['drafts'][0])
        self.assertEqual(draft['payload'], ERRATUM)
        self.assertEqual(draft['proposer'], 'call-1')
        meta = load_json(self.project / '.storyloop/state/telemetry/calls/call-1/meta.json')
        self.assertEqual(meta['errata'], result)

    def test_invalid_semantic_proposals_are_dropped(self):
        for key, value in [('claim', '文档里没有这句话'), ('evidence', ['app/clock.py:9999']),
                           ('document', ERRATA_PATH), ('document', '../README.md'),
                           ('evidence', []), ('correction', '')]:
            with self.subTest(key=key, value=value):
                result = self.capture(**{key: value})
                self.assertEqual(result['drafts'], [])
                self.assertIn(key, result['dropped'][0]['reason'])

    def test_maximum_and_unsafe_call(self):
        result = capture_errata(self.project, {'errata': [ERRATUM] * 7}, 'review', 'call', None)
        self.assertEqual(len(result['drafts']), 5)
        self.assertEqual([x['index'] for x in result['dropped']], [5, 6])
        with self.assertRaises(StoryLoopError):
            capture_errata(self.project, {'errata': [ERRATUM]}, 'review', '../escape', None)

    def test_landing_appends_and_opens_upgradeable_base(self):
        original = (self.project / ERRATUM['document']).read_bytes()
        result = approve_draft(self.project, 'errata/' + self.capture()['drafts'][0])
        content = (self.project / ERRATA_PATH).read_text(encoding='utf-8')
        self.assertTrue(content.startswith('# 知识库勘误\n'))
        self.assertIn('## E-0001', content)
        self.assertIn(ERRATUM['claim'], content)
        self.assertEqual((self.project / ERRATUM['document']).read_bytes(), original)
        self.assertEqual(upgrade_baseline(self.project, result['base_proposal'])['status'], 'BASELINE_UPGRADED')
        second = approve_draft(self.project, 'errata/' + self.capture()['drafts'][0])
        self.assertEqual(second['errata_id'], 'E-0002')
        self.assertTrue((self.project / ERRATA_PATH).read_text(encoding='utf-8').startswith(content))
        self.assertEqual(foundation_payload(self.project)['documents'][0], ERRATA_PATH)

    def test_edited_draft_revalidated(self):
        ref = 'errata/' + self.capture()['drafts'][0]
        path = self.project / '.storyloop/drafts' / ref / 'draft.json'
        draft = load_json(path)
        draft['payload']['claim'] = '不存在'
        write_json(path, draft)
        with self.assertRaises(StoryLoopError):
            approve_draft(self.project, ref)
        self.assertEqual(load_draft(self.project, ref)['status'], 'pending')
        self.assertFalse((self.project / ERRATA_PATH).exists())

    def test_post_commit_failure_rolls_back(self):
        ref = 'errata/' + self.capture()['drafts'][0]
        parent = head(self.project)
        from storyloop import lesson_landing
        actual = lesson_landing.set_draft_status
        def fail(project, reference, status, *args):
            if status == 'approved':
                raise RuntimeError('postcommit')
            return actual(project, reference, status, *args)
        with mock.patch.object(lesson_landing, 'set_draft_status', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'postcommit'):
                approve_draft(self.project, ref)
        self.assertEqual(head(self.project), parent)
        self.assertFalse((self.project / ERRATA_PATH).exists())
        self.assertEqual(load_draft(self.project, ref)['status'], 'pending')

    def test_ids_ignore_prose_mentions(self):
        self.assertEqual(next_errata_id('mention E-9999\n## E-0003\n'), 'E-0004')

    def test_nested_guards_and_failure_discard(self):
        p = self.project
        def inner():
            result = self.capture()
            self.assertEqual(result['drafts'], [])
            self.assertFalse((p / '.storyloop/drafts/errata').exists())
        read_only_call(p, 'FEAT-1', lambda: read_only_call(p, 'FEAT-1', inner))
        self.assertEqual(len(list((p / '.storyloop/drafts/errata').iterdir())), 1)
        before = len(list((p / '.storyloop/drafts/errata').iterdir()))
        def bad():
            self.capture()
            raise RuntimeError('failed')
        with self.assertRaisesRegex(RuntimeError, 'failed'):
            read_only_call(p, 'FEAT-1', bad)
        self.assertEqual(len(list((p / '.storyloop/drafts/errata').iterdir())), before)

    def test_agent_written_drafts_still_taint(self):
        def bad():
            self.capture()
            path = self.project / '.storyloop/drafts/evil.json'
            write_json(path, {})
        with self.assertRaises(StoryLoopError) as caught:
            read_only_call(self.project, 'FEAT-1', bad)
        self.assertEqual(caught.exception.code, 'WORKSPACE_TAINTED')
        self.assertFalse((self.project / '.storyloop/drafts/errata').exists())

    def test_write_scope_waits_for_boundary_acceptance(self):
        with defer_errata(manual=True):
            write_safely(self.project, 'FEAT-1', self.capture)
        self.assertFalse((self.project / '.storyloop/drafts/errata').exists())
        with defer_errata(manual=True):
            write_safely(self.project, 'FEAT-1', self.capture)
            release_errata()
        self.assertTrue((self.project / '.storyloop/drafts/errata').is_dir())

    def test_real_survey_call_accepts_errata_without_lessons(self):
        config_path = self.project / '.storyloop/config.json'
        config = load_json(config_path)
        answer = json.dumps({'documents': [{'path': 'runtime.md', 'content': 'x'}], 'errata': [ERRATUM]})
        script = "import json,sys; r=json.load(sys.stdin); assert 'lessons' not in r['payload']; assert 'lessons_used' not in r['response_schema']['properties']; print(" + repr(answer) + ")"
        config.update(adapter='command', agent_command=[sys.executable, '-c', script])
        write_json(config_path, config)
        result = read_only_call(self.project, 'FEAT-1', lambda: invoke_project_agent(self.project, 'survey', {}))
        self.assertIn('documents', result)
        self.assertEqual(len(list((self.project / '.storyloop/drafts/errata').iterdir())), 1)

    def test_writer_rejects_agent_written_draft(self):
        def bad():
            self.capture()
            write_json(self.project / '.storyloop/drafts/evil.json', {})
            return {}
        with self.assertRaisesRegex(StoryLoopError, 'protected drafts'):
            write_safely(self.project, 'FEAT-1', bad)
        self.assertFalse((self.project / '.storyloop/drafts/errata').exists())

    def test_outer_guard_failure_discards_successful_inner(self):
        def outer():
            read_only_call(self.project, 'FEAT-1', self.capture)
            (self.project / 'app/clock.py').write_text('changed\n')
        with self.assertRaises(StoryLoopError):
            read_only_call(self.project, 'FEAT-1', outer)
        self.assertFalse((self.project / '.storyloop/drafts/errata').exists())

    def test_forged_telemetry_is_not_a_queue(self):
        def forged():
            write_json(self.project / '.storyloop/state/telemetry/pending.json',
                       {'errata': [ERRATUM], 'drafts': ['D-FEAT-1-01']})
        read_only_call(self.project, 'FEAT-1', forged)
        self.assertFalse((self.project / '.storyloop/drafts/errata').exists())

    def test_actual_implement_entrypoint_discards_boundary_violation(self):
        from types import SimpleNamespace
        from storyloop import project_loop
        def implement(project, context):
            self.capture()
            (project / 'openspec/lessons').mkdir(parents=True)
            (project / 'openspec/lessons/L-0001.md').write_text('unauthorized')
            return {'summary': 'done', 'questions': ['which API?']}
        boundary = {'violations': [{'path': 'openspec/lessons/L-0001.md',
                                    'category': 'LESSON_PROTECTED'}], 'allowed': []}
        with mock.patch.object(project_loop, 'load_answers', return_value=[]), \
             mock.patch.object(project_loop.cost_resume, 'candidate', return_value=None), \
             mock.patch.object(project_loop.cost_resume, 'checkpoint'), \
             mock.patch.object(project_loop, 'record_boundary', return_value={'status': 'BOUNDARY_VIOLATION'}), \
             mock.patch.object(project_loop, 'audit_project_changes', return_value=boundary):
            result = project_loop._run_attempt(self.project, {'change_id': 'FEAT-1'},
                {'attempt': 1, 'failures': []}, SimpleNamespace(implement=implement), {'max_attempts': 3})
        self.assertEqual(result['status'], 'BOUNDARY_VIOLATION')
        self.assertFalse((self.project / '.storyloop/drafts/errata').exists())

    def test_external_head_advance_is_preserved_and_draft_pending(self):
        ref = 'errata/' + self.capture()['drafts'][0]
        from storyloop import lesson_landing
        actual = lesson_landing.set_draft_status
        external = []
        def fail(project, reference, status, *args):
            if status == 'approved':
                _git(project, 'commit', '--allow-empty', '-m', 'external advance')
                external.append(head(project))
                raise RuntimeError('postcommit')
            return actual(project, reference, status, *args)
        with mock.patch.object(lesson_landing, 'set_draft_status', side_effect=fail):
            with self.assertRaisesRegex(StoryLoopError, '外部 HEAD'):
                approve_draft(self.project, ref)
        self.assertEqual(head(self.project), external[0])
        self.assertTrue((self.project / ERRATA_PATH).is_file())
        pending = load_draft(self.project, ref)
        self.assertEqual(pending['status'], 'pending')
        self.assertIn('外部 HEAD', pending['failure'])

    def test_existing_errata_bytes_are_preserved(self):
        original = '# 知识库勘误\r\n\r\n## E-0007\r\n旧记录\r\n'.encode('utf-8')
        from storyloop.base_upgrade import propose_base_change
        proposal = propose_base_change(self.project, [ERRATA_PATH], '已有勘误')
        (self.project / ERRATA_PATH).write_bytes(original)
        _git(self.project, 'add', ERRATA_PATH)
        _git(self.project, 'commit', '-m', 'existing errata')
        upgrade_baseline(self.project, proposal['change_id'])
        result = approve_draft(self.project, 'errata/' + self.capture()['drafts'][0])
        self.assertEqual(result['errata_id'], 'E-0008')
        self.assertTrue((self.project / ERRATA_PATH).read_bytes().startswith(original))

    def test_questions_pause_after_clean_audit_and_keep_errata(self):
        from types import SimpleNamespace
        from storyloop import project_loop
        def implement(project, context):
            self.capture()
            return {'summary': 'question', 'questions': ['which API?']}
        with mock.patch.object(project_loop, 'load_answers', return_value=[]), \
             mock.patch.object(project_loop.cost_resume, 'candidate', return_value=None), \
             mock.patch.object(project_loop.cost_resume, 'checkpoint'), \
             mock.patch.object(project_loop, 'pause', return_value={'status': 'NEEDS_INPUT'}):
            result = project_loop._run_attempt(self.project, {'change_id': 'FEAT-1'},
                {'attempt': 1, 'failures': []}, SimpleNamespace(implement=implement), {'max_attempts': 3})
        self.assertEqual(result['status'], 'NEEDS_INPUT')
        self.assertEqual(len(list((self.project / '.storyloop/drafts/errata').iterdir())), 1)

    def test_draft_protection_precedes_generic_scratch_names(self):
        config_path = self.project / '.storyloop/config.json'
        config = load_json(config_path)
        config['profile']['scratch_paths'] = ['drafts']
        write_json(config_path, config)
        path = self.project / '.storyloop/drafts/evil.json'
        def bad():
            self.capture()
            write_json(path, {})
            return {}
        for guard in (read_only_call, write_safely):
            with self.subTest(guard=guard.__name__):
                with self.assertRaisesRegex(StoryLoopError, 'protected drafts'):
                    guard(self.project, 'FEAT-1', bad)
                self.assertFalse((self.project / '.storyloop/drafts/errata').exists())
                path.unlink()
