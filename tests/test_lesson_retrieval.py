import unittest
from storyloop.lesson_retrieval import select_lessons, claimed_lessons, change_paths
from tests.lesson_support import sample


class SelectionTests(unittest.TestCase):
    def test_role_scope_and_limit(self):
        entries = [sample(id=f'L-{i:04d}', scope=['app/**']) for i in range(1, 13)]
        entries.append(sample(id='L-0099', scope=['docs/**']))
        result = select_lessons(entries, 'implement', ['app/x.py'], set())
        self.assertEqual(len(result['relevant']), 10)
        self.assertEqual(result['omitted'], 2)
        self.assertEqual(set(result['relevant'][0]), {'id', 'summary', 'path', 'status'})

    def test_ordering(self):
        entries = [sample(id='L-0001', strength='check'), sample(id='L-0002'),
                   sample(id='L-0003'), sample(id='L-0004', strength='knowledge')]
        self.assertEqual([x['id'] for x in select_lessons(entries, 'implement', [], {'L-0003'})['relevant']],
                         ['L-0002', 'L-0004', 'L-0001', 'L-0003'])

    def test_core_and_stale_core_and_retired(self):
        entries = [sample(id='L-0001', tier='core', roles=['review'], scope=['other/**']),
                   sample(id='L-0002', tier='core'), sample(id='L-0003', status='retired')]
        result = select_lessons(entries, 'implement', [], {'L-0002'})
        self.assertEqual(result['core'][0]['body'], '正文\n')
        self.assertEqual([x['id'] for x in result['relevant']], ['L-0002'])
        self.assertEqual(result['relevant'][0]['status'], 'needs-review')

    def test_empty_paths_and_claim_deduplication(self):
        result = select_lessons([sample(), sample(id='L-0002', scope=['app/**'])], 'implement', [], set())
        self.assertEqual(len(result['relevant']), 1)
        claims = {'lessons_used': [{'id': x, 'how': 'x'} for x in ['L-0001', 'L-0404', 'L-0001']]}
        self.assertEqual(claimed_lessons(result, claims), (['L-0001'], ['L-0404']))


class MemoryCompatibilityTests(unittest.TestCase):
    def test_all_special_schemas_and_engine_length_limit(self):
        from storyloop.role_schemas import schema_for, schema_errors, wire_schema
        for role, mode in [('review', 'verify'), ('review', 'consistency'),
                           ('design', 'routing-plan'), ('acceptance', 'smoke-maintenance')]:
            schema = schema_for(role, {'mode': mode})
            self.assertIn('lessons_used', schema['properties'])
            self.assertIn('errata', schema['properties'])
        self.assertNotIn('lessons_used', schema_for('survey', {})['properties'])
        self.assertNotIn('lessons_used', schema_for('review', {'mode': 'retrospective'})['properties'])
        schema = schema_for('implement', {})
        reply = {'summary': 'ok', 'questions': [], 'lessons_used': [{'id': 'L-0001', 'how': 'x' * 201}]}
        self.assertTrue(schema_errors(reply, schema))
        self.assertNotIn('x-maxLength', str(wire_schema(schema)))

    def test_frozen_scope_diff_and_context_hash(self):
        import hashlib
        import shutil
        from tests.lesson_support import git_repo, sample, head, _git, commit_file
        from storyloop.storage import write_json
        from storyloop.call_context import context_manifest
        root = git_repo(lessons=[sample()])
        self.addCleanup(shutil.rmtree, root)
        source = head(root)
        _git(root, 'checkout', '-b', 'feature/test')
        commit_file(root, 'app/new.py', 'new')
        write_json(root / '.storyloop/state/runs/FEAT-1.json', {
            'source_head': source, 'contract_commit': source, 'feature_branch': 'feature/test',
            'routing': {'scope': {'touched': ['app/old.py'], 'creates': ['app/new.py']}}})
        self.assertEqual(change_paths(root, 'FEAT-1'), ['app/old.py', 'app/new.py'])
        path = 'openspec/lessons/L-0001.md'
        frozen = (root / path).read_bytes()
        (root / path).write_text('tampered', encoding='utf-8')
        manifest = context_manifest(root, {'change_id': 'FEAT-1', 'lessons': {
            'core': [], 'relevant': [{'path': path}]}}, [])
        self.assertEqual(manifest['lessons'][0]['sha256'], hashlib.sha256(frozen).hexdigest())

    def test_missing_feature_branch_uses_assessment_scope_without_skipping_lessons(self):
        import shutil
        from tests.lesson_support import git_repo, head, _git
        from storyloop.storage import write_json
        from storyloop.lesson_retrieval import lessons_payload
        root = git_repo(lessons=[sample(scope=['app/**'])])
        self.addCleanup(shutil.rmtree, root)
        # A descendant ref must not be mistaken for the absent exact branch.
        _git(root, 'branch', 'feature/missing/child')
        write_json(root / '.storyloop/state/runs/FEAT-1.json', {
            'source_head': head(root), 'contract_commit': head(root),
            'feature_branch': 'feature/missing',
            'routing': {'assessment': {'scope': {'touched': ['app/old.py'], 'creates': ['app/new.py']}}}})
        self.assertEqual(change_paths(root, 'FEAT-1'), ['app/old.py', 'app/new.py'])
        payload, reason = lessons_payload(root, 'implement', {'change_id': 'FEAT-1'})
        self.assertIsNone(reason)
        self.assertEqual([row['id'] for row in payload['relevant']], ['L-0001'])

    def test_git_failure_in_change_paths_is_not_misreported_as_invalid_lessons(self):
        import shutil
        from unittest.mock import patch
        from tests.lesson_support import git_repo, head
        from storyloop.storage import write_json
        from storyloop.lesson_retrieval import lessons_payload
        from storyloop.models import StoryLoopError
        root = git_repo(lessons=[sample()])
        self.addCleanup(shutil.rmtree, root)
        write_json(root / '.storyloop/state/runs/FEAT-1.json', {
            'source_head': head(root), 'contract_commit': head(root), 'feature_branch': 'feature/test'})
        failure = StoryLoopError('Git failed safely', code='GIT_BLOCKED')
        with patch('storyloop.lesson_retrieval._git', side_effect=failure):
            with self.assertRaises(StoryLoopError) as raised:
                lessons_payload(root, 'implement', {'change_id': 'FEAT-1'})
        self.assertIs(raised.exception, failure)

    def test_product_entrypoint_accepts_memory_metadata(self):
        from storyloop.models import validate_product
        from tests.support import product
        answer = {**product(), 'lessons_used': [{'id': 'L-0001', 'how': 'checked'}], 'errata': []}
        validate_product(answer)

    def test_chinese_feature_diff_is_selected_without_frozen_scope(self):
        import shutil
        from tests.lesson_support import git_repo, head, _git, commit_file
        from storyloop.storage import write_json
        from storyloop.lesson_retrieval import lessons_payload
        root = git_repo(lessons=[sample(scope=['app/中文/**'])])
        self.addCleanup(shutil.rmtree, root)
        source = head(root)
        _git(root, 'checkout', '-b', 'feature/chinese')
        commit_file(root, 'app/中文/服务.py', 'changed')
        write_json(root / '.storyloop/state/runs/FEAT-1.json', {
            'source_head': source, 'contract_commit': source, 'feature_branch': 'feature/chinese',
            'routing': {'scope': {'touched': [], 'creates': []}}})
        self.assertEqual(change_paths(root, 'FEAT-1'), ['app/中文/服务.py'])
        payload, reason = lessons_payload(root, 'implement', {'change_id': 'FEAT-1'})
        self.assertIsNone(reason)
        self.assertEqual([row['id'] for row in payload['relevant']], ['L-0001'])
