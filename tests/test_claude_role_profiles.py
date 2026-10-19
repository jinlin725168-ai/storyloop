"""Exercise profile dispatch through the example adapter, not just permission helpers."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_claude_adapter import adapter, Completed, envelope


class ClaudeRoleProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = self.tmp.name
        self.calls = []

    def request(self, role, perspectives=None, explore=None, lanes=False):
        return {'role': role, 'project': self.project, 'instructions': 'work', 'payload': {},
                'role_profile': {'version': 1, 'tier': 'strong', 'routing_tier': 'heavy', 'skills': [],
                    'writable_paths': ['backend/**', 'web/**'] if role == 'implement' else [],
                    'forbidden_paths': ['backend/base.py'], 'fast_checks': [],
                    'subagents': {'perspectives': perspectives or [], 'explore': explore or [], 'lanes': lanes}}}

    def invoke(self, request, reply, flags=None):
        def run(command, **kwargs):
            self.calls.append((command, kwargs['input']))
            result = reply(kwargs['input'])
            return Completed(envelope('success', json.dumps(result), 'session', 0.1, 1))
        with patch.object(adapter.subprocess, 'run', side_effect=run), \
                patch.object(adapter.shutil, 'which', return_value='/usr/bin/claude'), \
                patch('sys.stdin', io.StringIO(json.dumps(request))), \
                patch('sys.stdout', new_callable=io.StringIO) as out, \
                patch('sys.stderr', new_callable=io.StringIO):
            code = adapter.main_with_args(['--tier-model', 'strong=opus', '--tier-model', 'standard=sonnet', *(flags or [])])
        self.assertEqual(code, 0)
        return json.loads(out.getvalue())

    def test_requested_review_perspectives_override_legacy_cli_flag(self):
        request = self.request('review', perspectives=['security', 'oracle-strength'])
        answer = {'verdict': 'pass', 'summary': 'ok', 'findings': [], 'questions': []}
        result = self.invoke(request, lambda prompt: answer, ['--review-perspectives', 'none'])
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(any('[review perspective: oracle-strength]' in prompt for _, prompt in self.calls))
        self.assertEqual(result['_telemetry']['subagents_applied'],
            {'mode': 'parallel', 'perspectives': 2, 'explore': 0, 'lanes': 0})
        self.assertEqual(result['_telemetry']['path_guard'], 'enforced')

    def test_design_runs_standard_explorers_and_hands_results_to_strong_lead(self):
        request = self.request('design', explore=['similar-features', 'call-chain'])
        result = self.invoke(request, lambda prompt: {'summary': 'evidence'})
        self.assertEqual(len(self.calls), 3)
        children = [command for command, prompt in self.calls if '[design exploration:' in prompt]
        self.assertTrue(all(command[command.index('--model') + 1] == 'sonnet' for command in children))
        lead_command, lead_prompt = self.calls[-1]
        self.assertEqual(lead_command[lead_command.index('--model') + 1], 'opus')
        delivered = json.loads(lead_prompt.split('Request:\n')[-1])
        self.assertEqual(set(delivered['payload']['exploration']), {'similar-features', 'call-chain'})
        self.assertEqual(result['_telemetry']['subagents_applied']['explore'], 2)

    def test_heavy_lanes_have_intersected_permissions_and_readonly_planner(self):
        request = self.request('implement', lanes=True)
        request['payload']['design_plan'] = {'touched': ['backend/a.py', 'web/a.js']}
        def reply(prompt):
            if adapter.LANE_PLAN_MARK in prompt:
                return {'lanes': [{'name': 'server', 'task': 'backend'}, {'name': 'client', 'task': 'web'}]}
            return {'summary': 'ok', 'questions': [], 'design': 'done'}
        result = self.invoke(request, reply, ['--implement-lanes', 'server=backend,client=web'])
        self.assertEqual(len(self.calls), 4)
        planner = self.calls[0][0]
        self.assertEqual(planner[planner.index('--tools') + 1], 'Read,Grep,Glob,Bash')
        for command, prompt in self.calls:
            allowed = command[command.index('--allowedTools') + 1:command.index('--disallowedTools')]
            if '[implement lane: server]' in prompt:
                self.assertIn('Edit(/backend/**)', allowed)
                self.assertNotIn('Edit(/web/**)', allowed)
            if '[implement lane: client]' in prompt:
                self.assertIn('Edit(/web/**)', allowed)
                self.assertNotIn('Edit(/backend/**)', allowed)
        self.assertEqual(result['_telemetry']['subagents_applied']['lanes'], 2)

    def test_light_profile_disables_legacy_lanes_and_review_flags(self):
        request = self.request('review')
        request['role_profile']['routing_tier'] = 'light'
        result = self.invoke(request, lambda _: {'summary': 'ok'}, ['--review-perspectives', 'security'])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result['_telemetry']['subagents_applied']['perspectives'], 0)
