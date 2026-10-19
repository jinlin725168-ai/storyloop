"""Actual invocation checks for coherent routing, snapshots and provenance."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.baseline import bootstrap_project
from storyloop.models import StoryLoopError
from storyloop.project_agents import invoke_project_agent
from storyloop.storage import load_json, write_json
from storyloop.telemetry import ledger_entries
from tests.test_baseline_behavior import repository


class RoleContextIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        repository(self.root)
        bootstrap_project(self.root)
        config = load_json(self.root / '.storyloop/config.json')
        config.update(adapters={'default': {'kind': 'manual'}})
        write_json(self.root / '.storyloop/config.json', config)

    def invoke(self, role, payload):
        with self.assertRaises(StoryLoopError) as raised:
            invoke_project_agent(self.root, role, payload)
        self.assertEqual(raised.exception.code, 'NEEDS_INPUT')
        return load_json(self.root / f'.storyloop/requests/project-{role}.json')

    def test_persisted_light_tier_controls_actual_settings_profile_and_subagents(self):
        write_json(self.root / '.storyloop/state/runs/FEAT-9.json', {'routing': {'effective_tier': 'light'}})
        request = self.invoke('implement', {'change_id': 'FEAT-9', 'routing_tier': 'heavy',
            'routing': {'effective_tier': 'heavy'}})
        self.assertEqual(request['role_profile']['routing_tier'], 'light')
        self.assertEqual(request['role_profile']['tier'], 'standard')
        self.assertFalse(request['role_profile']['subagents']['lanes'])
        self.assertEqual(ledger_entries(self.root, 'FEAT-9')[-1]['tier_requested'], 'standard')

    def test_single_snapshot_supplies_instruction_profile_and_ledger_even_on_mutation(self):
        path = self.root / '.claude/skills/oracle-craft/SKILL.md'
        path.parent.mkdir(parents=True)
        body = '---\nversion: 9.9.9\n---\nfrozen body'
        path.write_text(body, encoding='utf-8')
        from storyloop import skills
        original = skills._snapshot

        def capture(*args):
            snapshot = original(*args)
            path.write_text('---\nversion: 0\n---\nmutated body', encoding='utf-8')
            return snapshot

        with patch.object(skills, '_snapshot', side_effect=capture):
            request = self.invoke('review', {'change_id': 'FEAT-9'})
        self.assertIn('frozen body', request['instructions'])
        self.assertNotIn('mutated body', request['instructions'])
        expected = hashlib.sha256(body.encode()).hexdigest()
        self.assertEqual(request['role_profile']['skills'][0]['sha256'], expected)
        entry = ledger_entries(self.root, 'FEAT-9')[-1]
        self.assertEqual(entry['skills'][0]['sha256'], expected)
        meta = load_json(self.root / '.storyloop/state/telemetry/calls' / entry['call'] / 'meta.json')
        self.assertEqual(meta['context']['skills'][0]['sha256'], expected)
        self.assertNotIn('frozen body', json.dumps(meta['context']))
        self.assertIn('sequential analysis', request['instructions'])

    def test_context_only_tracks_delivered_allowlisted_files_and_truncation(self):
        from storyloop.call_context import context_manifest
        path = self.root / 'openspec/specs/demo/spec.md'
        path.parent.mkdir(parents=True)
        path.write_text('whole body', encoding='utf-8')
        context = context_manifest(self.root, {'living_specs': [{'path': path.relative_to(self.root).as_posix(),
            'content': 'partial', 'truncated': True}], 'unrelated': 'openspec/specs/other/spec.md'}, [])
        self.assertEqual(context['living_specs'], [{'path': 'openspec/specs/demo/spec.md',
            'sha256': hashlib.sha256(b'partial').hexdigest()}])
        self.assertEqual(context['truncated'], ['openspec/specs/demo/spec.md'])
        self.assertNotIn('partial', json.dumps(context))
        self.assertEqual(context_manifest(self.root, {}, [])['living_specs'], [])

    def test_actual_heavy_invoke_uses_validated_cached_lane_prefixes(self):
        from tests.test_adapter_conformance import MOCK
        import sys
        script = self.root / 'scenario.json'
        write_json(script, {'capabilities': {'vendor': 'fixture', 'tiers': ['strong', 'standard'],
            'subagents': True, 'path_guard': True, 'fast_checks': True, 'modes': ['*'],
            'lanes': ['server', 'client'], 'lane_prefixes': {'server': 'src/api', 'client': 'web'}},
            'calls': [{'role': 'implement', 'result': {'summary': 'ok', 'questions': []}}]})
        config = load_json(self.root / '.storyloop/config.json')
        config['adapters']['default'] = {'kind': 'command', 'agent_command': [sys.executable, str(MOCK), '--script', str(script)]}
        write_json(self.root / '.storyloop/config.json', config)
        write_json(self.root / '.storyloop/state/runs/FEAT-9.json', {'routing': {'effective_tier': 'heavy'}})
        invoke_project_agent(self.root, 'implement', {'change_id': 'FEAT-9',
            'design_plan': {'touched': ['src/api/a.py', 'web/a.js']}})
        delivered = load_json(next((self.root / 'requests').glob('*-implement.json')))
        self.assertTrue(delivered['role_profile']['subagents']['lanes'])
        self.assertEqual(delivered['role_profile']['routing_tier'], 'heavy')
        from storyloop.adapter_capabilities import ensure_checked
        self.assertEqual(ensure_checked(self.root)['status'], 'ADAPTERS_CACHED')
