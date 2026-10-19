"""Shared protocol properties, exercising real adapters and offline native subprocesses."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from storyloop.adapter_capabilities import probe
from storyloop.models import StoryLoopError
from storyloop import project_agents as agents
from storyloop.role_schemas import schema_for, wire_schema
from storyloop.storage import load_json, write_json

SOURCE = Path(__file__).resolve().parents[1]
FIXTURES = SOURCE / 'tests/fixtures/fake_cli'
MOCK = SOURCE / 'storyloop/testing/mock_adapter.py'
CAPABILITIES = {'vendor': 'fixture', 'tiers': ['strong', 'standard'], 'subagents': False,
                'path_guard': False, 'fast_checks': False, 'modes': ['*']}
PROFILE = {'version': 1, 'tier': 'strong', 'routing_tier': 'standard', 'skills': [],
           'subagents': {'perspectives': ['security'], 'explore': [], 'lanes': False},
           'writable_paths': [], 'forbidden_paths': [], 'fast_checks': []}
ANSWER = {'verdict': 'pass', 'summary': 'checked', 'findings': [], 'questions': []}


class Conformance:
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        (self.root / '.storyloop').mkdir()
        self.script = self.root / 'script.json'
        self.config = {'kind': self.kind, 'agent_timeout_seconds': 10,
                       'models': {'strong': 'fixture-strong', 'standard': 'fixture-standard'}}
        if self.kind == 'mock':
            self.config.update(kind='command', agent_command=[sys.executable, str(MOCK), '--script', str(self.script)])
        if self.kind == 'claude':
            self.config.update(kind='command', agent_command=[sys.executable, str(FIXTURES / 'claude_driver.py'),
                '--tier-model', 'strong=fixture-strong', '--tier-model', 'standard=fixture-standard'])
        self.schema = schema_for('review', {})
        self.request = {'protocol': 'storyloop.project-agent.v1', 'project': str(self.root),
                        'role': 'review', 'payload': {}, 'role_profile': PROFILE,
                        'response_schema': wire_schema(self.schema)}
        self.trace = {'instructions': 'Review evidence', 'stderr': '', 'sessions': [], 'telemetry': None}
        self.write_scenario()

    def write_scenario(self, result=None, exit_code=0):
        result = ANSWER if result is None else result
        write_json(self.root / 'scenario.json', {'result': result, 'exit': exit_code})
        write_json(self.script, {'capabilities': CAPABILITIES, 'calls': [{'role': 'review',
            'result': result, 'exit': exit_code, 'telemetry': {'vendor': 'fixture', 'model': 'fixture-strong',
            'tier_applied': 'strong', 'subagents_applied': {'mode': 'sequential', 'perspectives': 1,
                                                        'explore': 0, 'lanes': 0}}}]})

    def invoke(self):
        if self.kind == 'manual':
            try:
                agents._manual(self.root, self.request, self.schema, self.trace)
            except StoryLoopError as error:
                self.assertEqual(error.code, 'NEEDS_INPUT')
            envelope = load_json(self.root / '.storyloop/requests/project-review.json')
            answer = load_json(self.root / 'scenario.json')['result']
            write_json(self.root / '.storyloop/responses/project-review.json',
                       {'request_id': envelope['request_id'], 'result': answer})
            return agents._manual(self.root, self.request, self.schema, self.trace)
        if self.kind == 'codex':
            with patch.object(agents, '_codex_executable', return_value=[sys.executable, str(FIXTURES / 'codex.py')]):
                return agents._codex(self.root, self.request, self.config, self.schema, self.trace)
        return agents._command(self.root, self.request, self.config, self.schema, self.trace)

    def captured_request(self):
        if self.kind == 'manual':
            return load_json(self.root / '.storyloop/requests/project-review.json')
        if self.kind == 'mock':
            return load_json(next((self.root / 'requests').glob('*-review.json')))
        return json.loads(load_json(self.root / 'cli-request.json')['prompt'].split('Request:\n')[-1])

    def test_handshake_shape(self):
        capability = probe(self.root, 'fixture', self.config)
        self.assertTrue(capability['known'])
        if self.kind == 'manual':
            self.assertEqual(capability['tiers'], [])
            return
        self.assertTrue(set(capability) >= {'known', 'vendor', 'tiers', 'subagents', 'path_guard', 'fast_checks', 'modes'})
        self.assertEqual(set(capability['tiers']), {'strong', 'standard'})
        self.assertEqual(capability['subagents'], self.kind == 'claude')
        self.assertEqual(capability['path_guard'], self.kind == 'claude')

    def test_profile_transmitted(self):
        self.invoke()
        self.assertEqual(self.captured_request()['role_profile'], PROFILE)

    def test_tier_selects_model_argument(self):
        if self.kind in {'manual', 'mock'}:
            self.skipTest('No model process: manual input / scripted replay only')
        self.invoke()
        argv = load_json(self.root / 'cli-request.json')['argv']
        flag = '-m' if self.kind == 'codex' else '--model'
        self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index(flag) + 1], 'fixture-strong')

    def test_telemetry_describes_applied_model(self):
        result = self.invoke()
        if self.kind == 'manual':
            self.assertNotIn('_telemetry', result)
            return
        telemetry = result['_telemetry']
        self.assertEqual(telemetry['vendor'], {'mock': 'fixture', 'codex': 'openai', 'claude': 'anthropic'}[self.kind])
        self.assertEqual(telemetry['model'], 'fixture-strong')
        self.assertEqual(telemetry['tier_applied'], 'strong')

    def test_schema_violation_is_contract_invalid(self):
        self.write_scenario(result={'summary': 'invalid'})
        with self.assertRaises(StoryLoopError) as caught:
            self.invoke()
        self.assertEqual(caught.exception.code, 'AGENT_CONTRACT_INVALID')

    def test_process_failure_is_unavailable(self):
        if self.kind == 'manual':
            self.skipTest('Manual input has no process to fail')
        self.write_scenario(exit_code=7)
        with self.assertRaises(StoryLoopError) as caught:
            self.invoke()
        self.assertEqual(caught.exception.code, 'AGENT_UNAVAILABLE')
        self.assertEqual(self.captured_request()['role_profile'], PROFILE)

    def test_subagent_mode_matches_advertised_capability(self):
        if self.kind == 'manual':
            self.assertNotIn('_telemetry', self.invoke())
            return
        result = self.invoke()
        self.assertEqual(result['_telemetry']['subagents_applied'],
                         {'mode': 'parallel' if self.kind == 'claude' else 'sequential',
                          'perspectives': 1, 'explore': 0, 'lanes': 0})
        if self.kind == 'codex':
            self.assertIn('sequential', load_json(self.root / 'cli-request.json')['prompt'])


for _kind in ('manual', 'mock', 'codex', 'claude'):
    globals()[f'{_kind.title()}ConformanceTests'] = type(f'{_kind.title()}ConformanceTests',
                                                       (Conformance, unittest.TestCase), {'kind': _kind})


class MockScriptTests(unittest.TestCase):
    def test_missing_entry_is_clear_and_records_request(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'script.json'
            write_json(script, {'capabilities': CAPABILITIES, 'calls': []})
            result = subprocess.run([sys.executable, '-m', 'storyloop.testing.mock_adapter', '--script', str(script)],
                                    cwd=SOURCE, input=json.dumps({'role': 'review', 'payload': {'mode': 'verify'}}),
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertIn('review', result.stderr)
            self.assertIn('verify', result.stderr)
            self.assertEqual(len(list((script.parent / 'requests').glob('*.json'))), 1)

    def test_mode_nth_replay_and_creates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = root / 'script.json'
            write_json(script, {'calls': [
                {'role': 'review', 'mode': 'verify', 'nth': 1, 'result': {'confirmed': True}},
                {'role': 'review', 'mode': 'verify', 'nth': 2, 'result': {'confirmed': False},
                 'creates': [{'path': 'output.txt', 'content': 'created'}]},
                {'role': 'review', 'nth': 1, 'stdout': 'not json'}]})
            command = [sys.executable, '-m', 'storyloop.testing.mock_adapter', '--script', str(script)]
            request = {'role': 'review', 'project': str(root), 'payload': {'mode': 'verify'}}
            for expected in (True, False):
                completed = subprocess.run(command, cwd=SOURCE, input=json.dumps(request), text=True, capture_output=True)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(json.loads(completed.stdout), {'confirmed': expected})
            self.assertEqual((root / 'output.txt').read_text(), 'created')
            request['payload'] = {}
            completed = subprocess.run(command, cwd=SOURCE, input=json.dumps(request), text=True, capture_output=True)
            self.assertEqual(completed.stdout.strip(), 'not json')
            self.assertEqual(len(list((root / 'requests').glob('*.json'))), 3)

    def test_request_role_cannot_escape_record_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'script.json'
            write_json(script, {'calls': []})
            completed = subprocess.run([sys.executable, '-m', 'storyloop.testing.mock_adapter', '--script', str(script)],
                cwd=SOURCE, input=json.dumps({'role': '../escape'}), text=True, capture_output=True)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn('role', completed.stderr)
            self.assertFalse((script.parent / 'escape.json').exists())

    def test_creates_cannot_escape_project_or_change_control_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'project'
            root.mkdir()
            script = Path(folder) / 'script.json'
            for path in ('../escaped.txt', '.git/config', '.storyloop/config.json'):
                write_json(script, {'calls': [{'role': 'implement', 'creates': [{'path': path, 'content': 'bad'}]}]})
                completed = subprocess.run([sys.executable, str(MOCK), '--script', str(script)],
                    input=json.dumps({'role': 'implement', 'project': str(root)}), text=True, capture_output=True)
                self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertFalse((Path(folder) / 'escaped.txt').exists())
            self.assertFalse((root / '.git/config').exists())

    def test_sleep_is_contained_and_maps_to_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = root / 'script.json'
            write_json(script, {'calls': [{'role': 'review', 'sleep': 10}]})
            config = {'agent_command': [sys.executable, str(MOCK), '--script', str(script)],
                      'agent_timeout_seconds': 0.2}
            with self.assertRaises(StoryLoopError) as caught:
                agents._command(root, {'role': 'review'}, config, {}, {'instructions': ''})
            self.assertEqual(caught.exception.code, 'AGENT_UNAVAILABLE')
            self.assertIn('timed_out', str(caught.exception))

    def test_record_redacts_credentials_in_text_and_named_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'script.json'
            write_json(script, {'calls': [{'role': 'review'}]})
            request = {'role': 'review', 'instructions': 'token=synthetic-value',
                       'payload': {'api_key': 'synthetic-value'}}
            completed = subprocess.run([sys.executable, str(MOCK), '--script', str(script)],
                input=json.dumps(request), text=True, capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            record = next((script.parent / 'requests').glob('*.json')).read_text()
            self.assertNotIn('synthetic-value', record)
