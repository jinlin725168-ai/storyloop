"""Regression coverage for the final role-profile integration review."""
import io
import json
import os
from pathlib import Path, PureWindowsPath
import shlex
import tempfile
import unittest
from unittest.mock import patch

from storyloop.claude_permissions import _bash_rule, _check_rule, _executable_token
from storyloop.models import StoryLoopError
from storyloop.role_profiles import enabled, resolve_adapters, role_settings
from storyloop.role_schemas import schema_for, wire_schema
from storyloop.testing.mock_adapter import _create_files
from tests.test_claude_adapter import adapter, Completed, envelope


class ConfigurationBoundaryTests(unittest.TestCase):
    def test_explicit_invalid_objects_are_not_defaults(self):
        for value in (None, False, 0, '', [], ['x']):
            with self.subTest(value=value):
                with self.assertRaises(StoryLoopError):
                    role_settings({'roles': value}, 'review', 'standard')
                with self.assertRaises(StoryLoopError):
                    role_settings({'roles': {'review': value}}, 'review', 'standard')
                with self.assertRaises(StoryLoopError):
                    enabled({'roles': value})

    def test_explicit_invalid_adapter_objects_are_not_defaults(self):
        for value in (None, False, 0, '', [], {}):
            with self.subTest(value=value):
                with self.assertRaises(StoryLoopError):
                    enabled({'adapters': value})
                with self.assertRaises(StoryLoopError):
                    resolve_adapters({'adapters': value})

    def test_reason_must_be_nonblank_text_even_without_downgrade(self):
        for reason in (None, False, True, 0, [], ['x'], {}, '', ' \t\n'):
            for tier in ('standard', 'strong'):
                with self.subTest(reason=reason, tier=tier), self.assertRaises(StoryLoopError):
                    role_settings({'roles': {'review': {'tier': tier, 'reason': reason}}}, 'review', 'standard')
        result = role_settings({'roles': {'review': {'tier': 'standard', 'reason': '  deliberate  '}}}, 'review', None)
        self.assertEqual(result['reason'], 'deliberate')
        self.assertEqual(role_settings({}, 'review', None)['tier'], 'strong')

    def test_downgrade_report_keeps_normalized_reason(self):
        from storyloop.report_evidence import _role_downgrades
        from storyloop.storage import write_json
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_json(root / '.storyloop/config.json', {'roles': {
                'review': {'tier': 'standard', 'reason': '  deliberate  '},
                'design': {'tier': 'standard', 'reason': '  '}}})
            self.assertEqual(_role_downgrades(root), [
                {'role': 'review', 'tier': 'standard', 'reason': 'deliberate'}])

    def test_invalid_kind_is_domain_error(self):
        for kind in ([], {}, False, None, 1):
            with self.subTest(kind=kind), self.assertRaises(StoryLoopError):
                resolve_adapters({'adapters': {'default': {'kind': kind}}})


class PermissionBoundaryTests(unittest.TestCase):
    def test_trusted_python_paths_are_single_bash_tokens(self):
        values = [(r'C:\Python312\python.exe', 'C:/Python312/python.exe'),
                  (r'C:\Program Files\Python312\python.exe', 'C:/Program Files/Python312/python.exe')]
        with tempfile.TemporaryDirectory(prefix='python path ') as folder:
            executable = Path(folder) / 'python'
            executable.write_text('placeholder', encoding='utf-8')
            # The real file is resolved by the engine (a Windows temp folder may be an 8.3 short name).
            values.append((str(executable), executable.resolve().as_posix()))
            for index, (value, expected) in enumerate(values):
                with self.subTest(value=value):
                    if index < 2:
                        # Pure path encoding only: native filesystem checks need Windows CI.
                        rule = _executable_token(PureWindowsPath(value)) + ' -m unittest:*'
                    else:
                        with patch('storyloop.claude_permissions.sys.executable', value):
                            rule = _check_rule(Path(folder), ['{python}', '-m', 'unittest'])
                    self.assertEqual(shlex.split(rule[:-2]), [expected, '-m', 'unittest'])

    @unittest.skipUnless(os.name == 'posix', 'POSIX Bash execution only')
    def test_space_path_runs_as_one_executable_in_bash(self):
        import subprocess
        with tempfile.TemporaryDirectory(prefix='python path ') as folder:
            executable = Path(folder) / 'python'
            executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n', encoding='utf-8')
            executable.chmod(0o700)
            with patch('storyloop.claude_permissions.sys.executable', str(executable)):
                rule = _check_rule(Path(folder), ['{python}', '-m', 'unittest'])
            result = subprocess.run(['bash', '-c', rule[:-2]], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), ['-m', 'unittest'])

    def test_untrusted_prefixes_stay_strict(self):
        with tempfile.TemporaryDirectory() as folder:
            for value in ('a;echo', 'a$(echo)', r'C:\Python\python.exe', 'a b', '../x', 'a*', 'a|b'):
                with self.subTest(value=value), self.assertRaises(StoryLoopError):
                    _check_rule(Path(folder), [value])
            with self.assertRaises(StoryLoopError):
                _bash_rule(Path(folder), "'path with spaces/python':*")

    def test_quoted_fast_check_survives_lane_permission_rebuild(self):
        from storyloop.claude_permissions import profile_command
        from storyloop.claude_profiles import lane_command, readonly_command
        with tempfile.TemporaryDirectory(prefix='python path ') as folder:
            root = Path(folder)
            executable = root / 'python'
            executable.write_text('placeholder', encoding='utf-8')
            request = {'project': folder, 'role': 'implement', 'role_profile': {
                'writable_paths': ['src/**'], 'fast_checks': [['{python}', '-m', 'unittest']]}}
            with patch('storyloop.claude_permissions.sys.executable', str(executable)):
                base = profile_command('claude', request, 80, [], 'opus')
                lane = lane_command(base, request, 'src')
                readonly = readonly_command(base, request)
            expected = 'Bash(' + shlex.quote(executable.resolve().as_posix()) + ' -m unittest:*)'
            self.assertIn(expected, lane)
            self.assertIn(expected, readonly)
            self.assertNotIn('Edit(/src/**)', readonly)

    def test_user_link_traversal_and_rule_syntax_stay_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'real').write_text('safe', encoding='utf-8')
            os.link(root / 'real', root / 'hard')
            with self.assertRaises(StoryLoopError):
                _check_rule(root, ['./hard'])
            for value in ('C:/Python/../python.exe', 'C:/Python*/python.exe', 'C:/a;echo/python.exe'):
                with self.subTest(value=value), self.assertRaises(StoryLoopError):
                    _executable_token(PureWindowsPath(value))



class MockCreatesBoundaryTests(unittest.TestCase):
    def test_control_aliases_and_external_links_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as outside:
            root = Path(folder)
            for index, destination in enumerate((root / '.git', root / '.storyloop', Path(outside))):
                destination.mkdir(exist_ok=True)
                alias = root / f'alias{index}'
                try:
                    alias.symlink_to(destination, target_is_directory=True)
                except OSError:
                    self.skipTest('symlink creation unavailable')
                with self.subTest(destination=destination), self.assertRaises((StoryLoopError, ValueError)):
                    _create_files({'project': folder}, [{'path': alias.name + '/probe', 'content': 'bad'}])
                self.assertFalse((destination / 'probe').exists())

    def test_reparse_directory_is_rejected_before_creating(self):
        import stat
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()  # lstat sees resolved paths (Windows temp may be an 8.3 name)
            (root / 'junction').mkdir()
            original = os.lstat

            def metadata(path, *args, **kwargs):
                result = original(path, *args, **kwargs)
                if Path(path) == root / 'junction':
                    return SimpleNamespace(st_mode=result.st_mode, st_nlink=result.st_nlink,
                                           st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
                return result

            with patch('storyloop.path_security.os.lstat', side_effect=metadata):
                with self.assertRaisesRegex(StoryLoopError, 'reparse'):
                    _create_files({'project': folder}, [{'path': 'junction/probe', 'content': 'bad'}])
            self.assertFalse((root / 'junction/probe').exists())

    def test_control_case_aliases_and_hardlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for value in ('.GIT/probe', '.StoryLoop/probe'):
                with self.subTest(value=value), self.assertRaises((StoryLoopError, ValueError)):
                    _create_files({'project': folder}, [{'path': value, 'content': 'bad'}])
            (root / 'original').write_text('safe', encoding='utf-8')
            os.link(root / 'original', root / 'alias')
            with self.assertRaises((StoryLoopError, ValueError)):
                _create_files({'project': folder}, [{'path': 'alias', 'content': 'bad'}])
            self.assertEqual((root / 'original').read_text(), 'safe')
            _create_files({'project': folder}, [{'path': 'src/new.py', 'content': 'ok'}])
            self.assertEqual((root / 'src/new.py').read_text(), 'ok')


class VerifyEntryBoundaryTests(unittest.TestCase):
    def test_verify_is_single_strong_session_with_its_own_schema(self):
        answer = {'results': [{'id': 'F-1', 'confirmed': True, 'reason': 'reproduced'}]}
        for routing in ('standard', 'heavy'):
            request = {'role': 'review', 'project': '/tmp/p', 'instructions': 'verify',
                       'payload': {'mode': 'verify', 'findings': [{'id': 'F-1'}]},
                       'response_schema': wire_schema(schema_for('review', {'mode': 'verify'})),
                       'role_profile': {'tier': 'strong', 'routing_tier': routing,
                                        'subagents': {'perspectives': ['security']}}}
            with self.subTest(routing=routing), \
                    patch.object(adapter.subprocess, 'run', return_value=Completed(
                        envelope('success', json.dumps(answer), 'verify', .1, 1))) as run, \
                    patch.object(adapter.shutil, 'which', return_value='/usr/bin/claude'), \
                    patch('sys.stdin', io.StringIO(json.dumps(request))), \
                    patch('sys.stdout', new_callable=io.StringIO) as out, \
                    patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(adapter.main_with_args(['--tier-model', 'strong=opus']), 0)
            result = json.loads(out.getvalue())
            self.assertEqual(result.get('results'), answer['results'])
            self.assertEqual(run.call_count, 1)
            command, prompt = run.call_args.args[0], run.call_args.kwargs['input']
            self.assertEqual(command[command.index('--model') + 1], 'opus')
            self.assertEqual(json.loads(command[command.index('--json-schema') + 1]), request['response_schema'])
            self.assertIn('confirmed', prompt)
            self.assertNotIn(adapter.SCHEMA_HINTS['review'], prompt)
            self.assertNotIn(adapter.LEAD_RULES, prompt)
            self.assertEqual(result['_telemetry']['subagents_applied']['perspectives'], 0)

    def test_degrade_verify_uses_engine_profile_schema_and_cost_ledger(self):
        from storyloop.project_agents import invoke_project_agent
        from storyloop.review_context import strong_verification
        from storyloop.storage import load_json, write_json
        from storyloop.telemetry import ledger_entries
        from tests.test_cross_vendor import configured, capabilities
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = configured(root, 'degrade')
            config['adapters']['a'] = dict(config['adapters']['a'])
            driver = Path(__file__).parent / 'fixtures/fake_cli/claude_driver.py'
            config['adapters']['a']['agent_command'] = [
                '{python}', str(driver.resolve()), '--tier-model', 'strong=opus', '--tier-model', 'standard=sonnet']
            config['adapters']['b']['agent_command'] = [str(root / 'missing-executable')]
            config['roles']['review'].update(tier='standard', reason='verify on findings')
            config['limits'] = {'max_calls': 2}
            write_json(root / '.storyloop/config.json', config)
            answer = {'results': [{'id': 'F-1', 'confirmed': True, 'reason': 'reproduced'}]}
            write_json(root / 'scenario.json', {'result': answer})
            with patch('storyloop.adapter_capabilities.probe', side_effect=[
                    {**capabilities('anthropic'), 'subagents': True}, capabilities('openai')]):
                with strong_verification():
                    result = invoke_project_agent(root, 'review', {'change_id': 'FEAT-1', 'mode': 'verify'})
                with self.assertRaises(StoryLoopError) as caught:
                    invoke_project_agent(root, 'review', {'change_id': 'FEAT-1', 'mode': 'verify'})
            self.assertEqual(caught.exception.code, 'COST_LIMIT_REACHED')
            self.assertEqual(result, answer)
            entries = ledger_entries(root, 'FEAT-1')
            self.assertEqual(len(entries), 2)
            self.assertTrue(entries[0]['failed'])
            self.assertEqual(entries[1]['adapter'], 'a')
            self.assertEqual(entries[1]['degraded'], 'cross_vendor')
            self.assertEqual(entries[1]['tier_requested'], 'strong')
            self.assertEqual(entries[1]['tier_applied'], 'strong')
            self.assertEqual(entries[1]['subagents_applied']['perspectives'], 0)
            recorded = load_json(root / 'cli-request.json')
            delivered = json.loads(recorded['prompt'].split('Request:\n')[-1])
            self.assertEqual(delivered['role_profile']['tier'], 'strong')
            self.assertEqual(delivered['role_profile']['routing_tier'], 'standard')
            self.assertEqual(recorded['argv'][recorded['argv'].index('--model') + 1], 'opus')
