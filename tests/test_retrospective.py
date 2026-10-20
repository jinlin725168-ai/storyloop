"""Retrospective drafts are guarded, bounded and never change the run verdict."""
import unittest
from unittest.mock import patch

from storyloop.retrospective import detect_signals, record_recurrence, retrospect_after_pass
from storyloop.storage import write_json, load_json
from storyloop.project_loop_support import save_run, run_path
from tests.lesson_support import initialized_project, sample


NOTHING = dict(lessons=[], errata=[], uncovered_concepts=[], nothing_to_learn='No reusable lesson')


class RetrospectiveTests(unittest.TestCase):
    def setUp(self):
        self.project = initialized_project()
        self.change = {'change_id': 'FEAT-1'}
        self.state = {'change_id': 'FEAT-1', 'status': 'PASSED', 'failures': []}
        save_run(self.project, self.state)

    def run_retro(self, reply=NOTHING):
        with patch('storyloop.retrospective.invoke_project_agent', return_value=reply) as call:
            retrospect_after_pass(self.project, self.change, self.state)
        return call

    def proposal(self):
        entry = sample(); entry.pop('id'); body = entry.pop('body')
        return dict(action='create', target=None, entry=entry, body=body,
                    why_needed='Without this, a cold reader assumes retries are harmless',
                    evidence=['README.md:1'], landing=None)

    def test_clean_pass_is_not_retrospected(self):
        call = self.run_retro()
        self.assertEqual(self.state['lessons']['retrospective'], 'SKIPPED_NO_SIGNAL')
        call.assert_not_called()

    def test_failed_then_fixed_produces_lesson_draft(self):
        self.state['failures'] = [{}]
        self.run_retro({**NOTHING, 'lessons': [self.proposal()]})
        result = self.state['lessons']
        self.assertEqual(result['retrospective'], 'DONE')
        self.assertEqual(result['signals'], [{'kind': 'failed_then_fixed', 'where': 'failures'}])
        self.assertTrue(result['drafts'][0].startswith('lesson/D-FEAT-1-'))
        draft = load_json(self.project / '.storyloop/drafts' / result['drafts'][0] / 'draft.json')
        self.assertNotIn('id', draft['payload']['entry'])

    def test_retrospective_failure_keeps_passed(self):
        self.state['failures'] = [{}]
        with patch('storyloop.retrospective.invoke_project_agent', side_effect=RuntimeError('unavailable')):
            retrospect_after_pass(self.project, self.change, self.state)
        self.assertEqual(self.state['status'], 'PASSED')
        self.assertEqual(self.state['lessons']['retrospective'], 'FAILED')

    def test_cost_limit_skips_retrospective(self):
        self.state['failures'] = [{}]
        path = self.project / '.storyloop/config.json'
        config = load_json(path); config['limits'] = {'max_calls': 0}; write_json(path, config)
        call = self.run_retro()
        call.assert_not_called()
        self.assertEqual(self.state['lessons']['retrospective'], 'SKIPPED_COST_LIMIT')

    def test_repeated_review_finding_counts_rounds(self):
        finding = {'message': 'same', 'location': 'README.md:1'}
        self.state['failures'] = [{'review': {'findings': [finding, finding]}}]
        self.assertNotIn('repeated_review_finding', [x['kind'] for x in detect_signals(self.project, self.change, self.state)])
        self.state['failures'] *= 2
        self.assertIn('repeated_review_finding', [x['kind'] for x in detect_signals(self.project, self.change, self.state)])

    def ledger(self, change, ids, **extra):
        write_json(self.project / '.storyloop/state/telemetry' / (change + '.json'),
                   {'version': 1, 'entries': [{'role': 'review', 'call': 'call-1', 'lessons_provided': ids, **extra}]})

    def test_recurrence_counts_once_per_change_and_requests_upgrade(self):
        for change in ('FEAT-1', 'FEAT-2'):
            self.ledger(change, ['L-0001'])
        self.assertEqual(record_recurrence(self.project, 'FEAT-1', {'failures': [{}]}), [])
        self.assertEqual(record_recurrence(self.project, 'FEAT-1', {'failures': [{}]}), [])
        self.assertEqual(record_recurrence(self.project, 'FEAT-2', {'failures': [{}]}), ['L-0001'])

    def test_all_summaries_and_total_limit(self):
        self.state['failures'] = [{}]
        concepts = [{'concept': 'Retries', 'evidence': ['README.md:1'],
                     'suggested_document': 'openspec/foundation/retries.md'}]
        call = self.run_retro({**NOTHING, 'lessons': [self.proposal()] * 3, 'uncovered_concepts': concepts})
        self.assertIsInstance(call.call_args.args[2]['lessons'], list)
        self.assertEqual(len(self.state['lessons']['drafts']), 3)
        self.assertEqual(len(self.state['lessons']['dropped']), 1)

    def test_readonly_violation_creates_no_drafts(self):
        self.state['failures'] = [{}]
        def mutate(*args):
            (self.project / 'README.md').write_text('changed')
            return {**NOTHING, 'lessons': [self.proposal()]}
        with patch('storyloop.retrospective.invoke_project_agent', side_effect=mutate):
            retrospect_after_pass(self.project, self.change, self.state)
        self.assertEqual(self.state['lessons']['retrospective'], 'FAILED')
        self.assertEqual(self.state['lessons']['drafts'], [])

    def test_truncated_evidence_recovers_all_and_rejects_missing_or_unsafe(self):
        ids = [f'L-{i:04d}' for i in range(25)]
        self.ledger('FEAT-1', ids[:20], lessons_truncated=True)
        path = self.project / '.storyloop/state/telemetry/calls/call-1/meta.json'
        write_json(path, {'lessons_provided': ids, 'lessons_claimed': [], 'lessons_unknown': ['invented-id']})
        record_recurrence(self.project, 'FEAT-1', {'failures': [{}]})
        self.assertEqual(len(load_json(self.project / '.storyloop/state/lessons.json')), 25)
        self.state['failures'] = [{}]
        write_json(path, {'lessons_provided': ids, 'lessons_claimed': [], 'lessons_unknown': 'bad'})
        self.run_retro()
        self.assertIn('损坏', self.state['lessons']['error'])
        path.unlink()
        self.run_retro()
        self.assertEqual(self.state['lessons']['retrospective'], 'FAILED')
        self.assertIn('缺失', self.state['lessons']['error'])
        self.ledger('FEAT-1', ids[:20], lessons_truncated=True, call='../escape')
        self.run_retro()
        self.assertIn('call_id', self.state['lessons']['error'])

    def test_invalid_proposals_are_dropped(self):
        self.state['failures'] = [{}]
        invalid = [{**self.proposal(), 'why_needed': ''},
                   {**self.proposal(), 'entry': {**self.proposal()['entry'], 'summary': 'x' * 121}},
                   {**self.proposal(), 'evidence': ['../README.md:1']}]
        self.run_retro({**NOTHING, 'lessons': invalid})
        self.assertEqual(self.state['lessons']['drafts'], [])
        self.assertEqual(len(self.state['lessons']['dropped']), 3)
        self.assertEqual(self.state['lessons']['retrospective'], 'DONE')

    def test_manual_superseded_and_active_rejected(self):
        from storyloop.retrospective import retrospect
        from storyloop.models import StoryLoopError
        self.state['status'] = 'SUPERSEDED'; save_run(self.project, self.state)
        with patch('storyloop.retrospective.load_change', return_value=self.change), patch(
                'storyloop.retrospective.invoke_project_agent', return_value=NOTHING):
            self.assertEqual(retrospect(self.project, 'FEAT-1', manual=True)['retrospective'], 'DONE')
            self.state['status'] = 'IMPLEMENTING'; save_run(self.project, self.state)
            with self.assertRaises(StoryLoopError):
                retrospect(self.project, 'FEAT-1', manual=True)

    def test_needs_spec_fix_in_predecessor_and_human_signals(self):
        old = {'status': 'SUPERSEDED', 'stop': {'status': 'NEEDS_SPEC_FIX'}}
        write_json(self.project / '.storyloop/state/runs/FIX-old.json', old)
        self.change['supersedes'] = {'change_id': 'FIX-old'}
        self.state.update(answers=[{}], resets=[{}], repairs=[{}])
        kinds = {s['kind'] for s in detect_signals(self.project, self.change, self.state)}
        self.assertEqual(kinds, {'needs_spec_fix', 'human_answer', 'reset', 'acceptance_repair'})

    def test_uncovered_concept_draft_has_foundation_landing(self):
        self.state['failures'] = [{}]
        self.run_retro({**NOTHING, 'uncovered_concepts': [{'concept': 'Retries',
            'evidence': ['README.md:1'], 'suggested_document': 'openspec/foundation/retries.md'}]})
        result = self.state['lessons']
        self.assertEqual(len(result['drafts']), 1)
        draft = load_json(self.project / '.storyloop/drafts' / result['drafts'][0] / 'draft.json')
        self.assertEqual(draft['payload']['entry']['strength'], 'knowledge')
        self.assertIn('## Retries', draft['payload']['landing']['append'])

    def test_real_command_adapter_errata_captured_once_after_guard(self):
        import sys
        from pathlib import Path
        from tests.lesson_support import commit_file, write_lesson, commit_all
        from storyloop.drafts import list_drafts
        commit_file(self.project, 'openspec/foundation/runtime.md', 'Clock is synchronous.\n')
        write_lesson(self.project, sample())
        commit_all(self.project, 'lesson')
        self.state['failures'] = [{}]
        script = self.project.parent / (self.project.name + '-retro-script.json')
        self.addCleanup(script.unlink)
        reply = {**NOTHING, 'lessons': [self.proposal()], 'errata': [{
            'document': 'openspec/foundation/runtime.md', 'claim': 'Clock is synchronous.',
            'correction': 'Clock is asynchronous.', 'evidence': ['README.md:1']}]}
        write_json(script, {'calls': [{'role': 'review', 'mode': 'retrospective', 'result': reply}]})
        config_path = self.project / '.storyloop/config.json'
        config = load_json(config_path)
        config.update(adapter='command', agent_command=[sys.executable,
            str(Path(__file__).resolve().parents[1] / 'storyloop/testing/mock_adapter.py'), '--script', str(script)])
        write_json(config_path, config)
        retrospect_after_pass(self.project, self.change, self.state)
        self.assertEqual(self.state['lessons']['retrospective'], 'DONE', self.state['lessons'])
        self.assertEqual(len(self.state['lessons']['drafts']), 2)
        self.assertEqual(len(list_drafts(self.project)), 2)
        from storyloop.telemetry import ledger_entries
        rows = ledger_entries(self.project, 'FEAT-1')
        self.assertEqual(rows[-1]['lessons_provided'], [])

    def test_cli_requires_change(self):
        from storyloop.cli import parser
        import contextlib, io
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser().parse_args(['lessons-propose', str(self.project)])
        self.assertEqual(parser().parse_args(['lessons-propose', str(self.project), '--change', 'FEAT-1']).change, 'FEAT-1')

    def test_real_loop_retrospects_only_after_passed_receipt_without_rewriting_report(self):
        from tests.test_project_loop import ProjectLoopTests, smoke_payload
        from storyloop.project_loop import LoopServices, run_change
        from pathlib import Path
        import hashlib
        fixture = ProjectLoopTests()
        temporary, root, change_id = fixture._approved()
        self.addCleanup(temporary.cleanup)
        attempts, observed = [], []
        def implement(project, context):
            attempts.append(context['attempt'])
            if len(attempts) == 2:
                path = project / 'modules/orders/service.py'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('VALUE = 1\n')
            return {'summary': 'done', 'questions': []}
        def retrospective(project, role, payload):
            disk = load_json(run_path(project, change_id))
            self.assertEqual(disk['status'], 'PASSED')
            self.assertIn('integration_commit', disk)
            report = Path(disk['report'])
            observed.append((report, hashlib.sha256(report.read_bytes()).hexdigest()))
            return NOTHING
        services = LoopServices(generate_smoke=lambda p, c: smoke_payload(change_id), implement=implement,
            review=lambda p, c: {'verdict': 'pass', 'summary': 'ok', 'findings': [], 'questions': []})
        with patch('storyloop.retrospective.invoke_project_agent', side_effect=retrospective):
            result = run_change(root, change_id, services)
        self.assertEqual(result['status'], 'PASSED')
        self.assertEqual(len(observed), 1)
        self.assertEqual(hashlib.sha256(observed[0][0].read_bytes()).hexdigest(), observed[0][1])
        self.assertEqual(load_json(run_path(root, change_id))['lessons']['retrospective'], 'DONE')

    def test_command_adapter_drops_structurally_invalid_proposals_individually(self):
        import json, sys
        entry = dict(self.proposal()['entry']); entry.pop('summary')
        reply = {**NOTHING, 'lessons': [{**self.proposal(), 'entry': entry},
                 {**self.proposal(), 'why_needed': 42, 'evidence': 'README.md:1'}, self.proposal()]}
        config_path = self.project / '.storyloop/config.json'
        config = load_json(config_path)
        script = 'import sys,json; json.load(sys.stdin); print(' + repr(json.dumps(reply)) + ')'
        config.update(adapter='command', agent_command=[sys.executable, '-c', script])
        write_json(config_path, config)
        self.state['failures'] = [{}]
        retrospect_after_pass(self.project, self.change, self.state)
        self.assertEqual(self.state['lessons']['retrospective'], 'DONE', self.state['lessons'])
        self.assertEqual(len(self.state['lessons']['drafts']), 1)
        self.assertEqual(len(self.state['lessons']['dropped']), 2)

    def test_cli_failed_retrospective_has_failure_exit_and_message(self):
        from storyloop.cli import main
        import io
        result = {'retrospective': 'FAILED', 'signals': [], 'drafts': [],
                  'upgrade_suggested': [], 'error': '无法读取完整证据'}
        with patch('storyloop.retrospective.retrospect', return_value=result), patch(
                'sys.stdout', new_callable=io.StringIO) as out:
            code = main(['lessons-propose', str(self.project), '--change', 'FEAT-1'])
        self.assertEqual(code, 2)
        self.assertIn('复盘失败', out.getvalue())
        self.assertIn('无法读取完整证据', out.getvalue())

    def test_deferred_validation_is_local_and_wire_schema_remains_strict(self):
        import json
        from storyloop.role_schemas import schema_for, wire_schema, schema_errors
        schema = schema_for('review', {'mode': 'retrospective'})
        wire = wire_schema(schema)
        self.assertNotIn('x-deferred-validation', json.dumps(wire))
        item = wire['properties']['lessons']['items']
        self.assertFalse(item['additionalProperties'])
        self.assertIn('why_needed', item['required'])
        self.assertTrue(schema_errors({**NOTHING, 'lessons': 'invalid'}, schema))
        self.assertTrue(schema_errors({**NOTHING, 'unexpected': True}, schema))
        self.assertTrue(schema_errors({'verdict': 1}, schema_for('review', {})))

    def test_command_adapter_drops_unknown_original_entry_body(self):
        import json, sys
        bad = {**self.proposal(), 'entry': {**self.proposal()['entry'], 'body': {'unexpected': True}}}
        reply = {**NOTHING, 'lessons': [bad, {**self.proposal(), 'entry': None}, self.proposal()]}
        path = self.project / '.storyloop/config.json'
        config = load_json(path)
        script = 'import sys,json; json.load(sys.stdin); print(' + repr(json.dumps(reply)) + ')'
        config.update(adapter='command', agent_command=[sys.executable, '-c', script])
        write_json(path, config)
        self.state['failures'] = [{}]
        retrospect_after_pass(self.project, self.change, self.state)
        self.assertEqual(self.state['lessons']['retrospective'], 'DONE', self.state['lessons'])
        self.assertEqual(len(self.state['lessons']['drafts']), 1)
        self.assertEqual(len(self.state['lessons']['dropped']), 2)
