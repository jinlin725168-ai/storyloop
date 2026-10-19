from pathlib import Path
import tempfile
import unittest
from storyloop.role_subagents import subagents_for
from storyloop.claude_permissions import profile_command
from storyloop.models import StoryLoopError


class RoleSubagentTests(unittest.TestCase):
    def test_three_tiers_and_roles(self):
        for tier, count in [('light', 0), ('standard', 4), ('heavy', 5)]:
            self.assertEqual(len(subagents_for('review', tier, {}, {})['perspectives']), count)
            self.assertEqual(len(subagents_for('design', tier, {}, {})['explore']),
                             {'light': 0, 'standard': 2, 'heavy': 3}[tier])
            self.assertEqual(subagents_for('implement', tier, {}, {})['explore'], [])

    def test_lanes_use_actual_prefixes_not_names(self):
        payload = {'design_plan': {'touched': ['src/api/a.py', 'web/view.ts']}}
        caps = {'lanes': ['backend', 'frontend'],
                'lane_prefixes': {'backend': 'src/api', 'frontend': 'web'}}
        self.assertTrue(subagents_for('implement', 'heavy', payload, caps)['lanes'])
        self.assertFalse(subagents_for('implement', 'standard', payload, caps)['lanes'])
        self.assertFalse(subagents_for('implement', 'heavy', payload, {'lanes': ['src', 'web']})['lanes'])

    def test_permission_rules_cover_writers_without_bare_write_allow(self):
        with tempfile.TemporaryDirectory() as folder:
            for role in ['implement', 'characterize']:
                request = {'role': role, 'project': folder, 'role_profile': {
                    'writable_paths': ['src/**'], 'forbidden_paths': ['src/base.py'],
                    'fast_checks': [['python', '-m', 'unittest']]}}
                command = profile_command('claude', request, 10, [], 'sonnet')
                start, end = command.index('--allowedTools') + 1, command.index('--disallowedTools')
                self.assertEqual(command[start:end], ['Read', 'Grep', 'Glob', 'Edit(/src/**)',
                    'Bash(python -m unittest:*)'])
                self.assertIn('Edit(/src/base.py)', command)
                self.assertNotIn('acceptEdits', command)
                self.assertEqual(command[command.index('--setting-sources') + 1], '')
                self.assertIn('--restricted', command)
                self.assertIn('--strict-mcp-config', command)
                self.assertNotIn('Write(/src/**)', command)

    def test_unsafe_shell_paths_and_links_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'real').mkdir()
            (root / 'link').symlink_to(root / 'real', target_is_directory=True)
            for value in ['src/../base/**', 'src;touch/**', 'src&&x/**', '$(x)/**', 'link/**']:
                request = {'role': 'implement', 'project': folder, 'role_profile': {
                    'writable_paths': [value], 'forbidden_paths': [], 'fast_checks': []}}
                with self.assertRaises(StoryLoopError):
                    profile_command('claude', request, 10, [], None)
            for value in ['python;rm:*', 'python&&x:*', '$(python):*', 'python ../x:*']:
                with self.assertRaises(StoryLoopError):
                    profile_command('claude', {'role': 'implement', 'project': folder,
                        'role_profile': {'writable_paths': [], 'forbidden_paths': [],
                                         'fast_checks': []}}, 10, [value], None)

    def test_nested_symlink_is_rejected_before_recursive_allow_is_issued(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'src').mkdir()
            (root / 'outside').mkdir()
            (root / 'src/link').symlink_to(root / 'outside', target_is_directory=True)
            request = {'role': 'implement', 'project': folder, 'role_profile': {
                'writable_paths': ['src/**'], 'forbidden_paths': [], 'fast_checks': []}}
            with self.assertRaises(StoryLoopError):
                profile_command('claude', request, 10, [], None)

    def test_permissions_and_boundary_audit_reject_protected_file_and_symlink(self):
        from tests.test_baseline_behavior import repository, git
        from storyloop.baseline import bootstrap_project
        from storyloop.project_boundaries import role_path_boundary, audit_project_changes
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / 'core.py').write_text('original', encoding='utf-8')
            git(root, 'add', 'core.py')
            git(root, 'commit', '-qm', 'base')
            bootstrap_project(root)
            boundary = role_path_boundary(root, 'implement', {})
            request = {'role': 'implement', 'project': folder, 'role_profile': {**boundary, 'fast_checks': []}}
            command = profile_command('claude', request, 10, [], None)
            self.assertIn('Edit(/core.py)', command[command.index('--disallowedTools'):])
            (root / 'core.py').write_text('changed', encoding='utf-8')
            (root / 'modules').mkdir(exist_ok=True)
            (root / 'modules/link.py').symlink_to(root / 'core.py')
            audit = audit_project_changes(root, 'FEAT-9')
            self.assertIn('UNSAFE_LINK', {row['category'] for row in audit['violations']})
            self.assertIn('core.py', {row['path'] for row in audit['violations']})

    def test_readonly_roles_never_receive_file_writers_or_fast_check_allowances(self):
        with tempfile.TemporaryDirectory() as folder:
            for role in ['product', 'acceptance', 'review', 'design', 'survey']:
                request = {'role': role, 'project': folder, 'role_profile': {'writable_paths': ['src/**'],
                    'forbidden_paths': [], 'fast_checks': [['python', '-m', 'unittest']]}}
                command = profile_command('claude', request, 10, [], None)
                allowed = command[command.index('--allowedTools') + 1:command.index('--disallowedTools')]
                self.assertEqual(allowed, ['Read', 'Grep', 'Glob'])
                inventory = command[command.index('--tools') + 1].split(',')
                self.assertEqual(inventory, ['Read', 'Grep', 'Glob'] +
                    (['Bash'] if role in {'review', 'design', 'survey'} else []))
