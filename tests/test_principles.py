import unittest
from tests.lesson_support import cleanup_repo
from storyloop.principles import validate_principles, load_principles
from storyloop.storage import load_json
from storyloop.base_upgrade import upgrade_baseline
from storyloop.lesson_landing import approve_draft, land_knowledge
from storyloop.models import StoryLoopError
from tests.lesson_support import initialized_project
from tests import test_lesson_landing


class PrinciplesTests(unittest.TestCase):
    def test_defaults_and_legacy(self):
        project = initialized_project()
        self.addCleanup(cleanup_repo, project)
        self.assertEqual(load_principles(project)['status'], 'NOT_CONFIGURED')
        self.assertEqual(validate_principles({'principles': []}), {'version': '0.0.0', 'changelog': []})
        self.assertEqual(validate_principles({'version': 1, 'principles': []})['version'], '0.0.0')

    def test_principles_landing_and_frozen_read(self):
        helper = test_lesson_landing.LandingTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        project = helper.project
        landing = {'kind': 'principles', 'principle': {'id': 'P1', 'text': '可验证'}, 'change': '新增'}
        result = approve_draft(project, helper.draft(strength='knowledge', landing=landing))
        with self.assertRaises(StoryLoopError):
            load_principles(project)
        upgrade_baseline(project, result['base_proposal'])
        self.assertEqual(validate_principles(load_json(project / 'openspec/principles.json'))['version'], '0.0.1')
        self.assertEqual(validate_principles(load_json(project / 'openspec/principles.json'))['changelog'][0]['source'], 'manual')

    def test_legacy_file_loads_from_baseline(self):
        from storyloop.baseline import bootstrap_project
        from tests.lesson_support import git_repo
        project = git_repo(files={'openspec/principles.json': '{"version":1,"principles":[{"id":"P1","text":"old"}]}'})
        self.addCleanup(cleanup_repo, project)
        bootstrap_project(project)
        self.assertEqual(load_principles(project)['principles'][0]['text'], 'old')
        self.assertEqual(validate_principles(load_json(project / 'openspec/principles.json'))['version'], '0.0.0')

    def test_invalid_versions_rejected(self):
        for version in (True, 2, '1.2', '01.2.3', None):
            with self.subTest(version=version), self.assertRaises(StoryLoopError):
                validate_principles({'version': version, 'principles': []})

    def test_principle_replaced_and_patch_incremented(self):
        helper = test_lesson_landing.LandingTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        for number, value in enumerate(('first', 'second'), 1):
            landing = {'kind': 'principles', 'principle': {'id': 'P1', 'text': value}, 'change': value}
            writes = land_knowledge(helper.project, {'payload': {'landing': landing}, 'source': 'manual'}, {})
            target = helper.project / 'openspec/principles.json'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(writes['openspec/principles.json'])
            self.assertEqual(validate_principles(load_json(target))['version'], f'0.0.{number}')
            self.assertEqual(load_json(target)['principles'], [landing['principle']])
