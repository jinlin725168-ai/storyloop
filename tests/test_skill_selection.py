from pathlib import Path
import tempfile
import unittest
from storyloop.models import StoryLoopError
from storyloop.skills import select_skills, instruction_skill_snapshot


class SkillSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def skill(self, name, meta='roles: [review]', body='body', target='.claude/skills'):
        path = self.root / target / name / 'SKILL.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'---\nname: {name}\nversion: 1.0.0\n{meta}\n---\n{body}', encoding='utf-8')
        return path

    def test_packaged_selection_is_legacy_equivalent(self):
        expected = {'survey': ['foundation-survey'], 'product': ['oracle-craft'],
                    'acceptance': ['oracle-craft'], 'characterize': ['oracle-craft'],
                    'review': ['oracle-craft'], 'design': [], 'implement': []}
        for role, names in expected.items():
            self.assertEqual(select_skills(self.root, role, []), names)

    def test_project_precedence_stacks_and_explicit_roles(self):
        self.skill('local', 'roles: [review]\nstacks: [python]')
        self.assertIn('local', select_skills(self.root, 'review', ['python']))
        self.assertNotIn('local', select_skills(self.root, 'review', ['java']))
        self.skill('oracle-craft', 'roles: [design]', 'actual local body')
        self.assertNotIn('oracle-craft', select_skills(self.root, 'review', []))
        self.assertIn('oracle-craft', select_skills(self.root, 'design', []))
        body, snap = instruction_skill_snapshot(self.root, 'oracle-craft')
        self.assertIn('actual local body', body)
        self.assertEqual(snap['path'], '.claude/skills/oracle-craft/SKILL.md')

    def test_legacy_project_copy_inherits_only_packaged_roles(self):
        self.skill('oracle-craft', '', 'legacy project body')
        self.skill('project-only', '', 'unassigned')
        self.assertEqual(select_skills(self.root, 'review', []), ['oracle-craft'])
        self.assertIn('legacy project body', instruction_skill_snapshot(self.root, 'oracle-craft')[0])

    def test_count_and_size_limits_fail_closed(self):
        for i in range(6):
            self.skill(f'local-{i}')
        with self.assertRaises(StoryLoopError):
            select_skills(self.root, 'review', [])
        with tempfile.TemporaryDirectory() as folder:
            self.root = Path(folder)
            self.skill('huge', body='x' * 200_001)
            with self.assertRaises(StoryLoopError):
                select_skills(self.root, 'review', [])

    def test_skill_symlink_and_traversal_are_rejected(self):
        path = self.skill('local')
        path.rename(path.with_name('real.md'))
        path.symlink_to(path.with_name('real.md'))
        with self.assertRaises(StoryLoopError):
            select_skills(self.root, 'review', [])
        with self.assertRaises(StoryLoopError):
            instruction_skill_snapshot(self.root, '../outside')

    def test_block_frontmatter_lists_are_supported_without_external_yaml(self):
        self.skill('block', 'roles:\n  - review\nstacks:\n  - python')
        self.assertIn('block', select_skills(self.root, 'review', ['python']))
        self.assertNotIn('block', select_skills(self.root, 'review', ['java']))
