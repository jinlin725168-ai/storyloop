"""External acceptance checks for design revision stop findings."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

from storyloop.testing.smoke_harness.v1 import HarnessError, precondition

PROBE = r'''
import contextlib
import io
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import sys
from storyloop.change_log import change_log, render_log
from storyloop.cli_output import display
from storyloop.product_cli import dispatch
from storyloop.run_stops import pause
from storyloop.stop_report import build_stop, render_stop
from storyloop.storage import load_json, write_json

request = json.loads(sys.stdin.read())
findings = request['findings']
with tempfile.TemporaryDirectory() as folder:
    project = Path(folder)
    change_id = 'FEAT-fixture'
    product = {'version': 1, 'goal': '核对停点意见', 'non_goals': [], 'questions': [],
               'stories': [{'id': 'ST-1', 'title': '核对停点', 'as_a': '用户',
                            'want': '查看意见', 'so_that': '修订方案', 'rules': [],
                            'out_of_scope': [], 'depends_on': [],
                            'acceptance': [{'id': 'AC-1', 'given': '有停点',
                                            'when': '查看', 'then': '展示意见',
                                            'kind': 'automated'}]}]}
    change = {'version': 1, 'change_id': change_id, 'status': 'APPROVED',
              'revision': 1, 'idea': '核对停点意见', 'product': product,
              'created_at': '2026-11-05T00:00:00Z',
              'updated_at': '2026-11-05T00:00:00Z',
              'history': [], 'approval': None}
    write_json(project / '.storyloop/state/changes' / (change_id + '.json'), change)
    state = {'change_id': change_id, 'status': 'IMPLEMENTING', 'attempt': 0,
             'failures': [], 'planning': {'consistency': {'findings': findings}},
             'agent_call_record': 'RAW_CALL_RECORD_SENTINEL'}
    run = pause(project, state, 'DESIGN_REVISION_REQUIRED',
                phase='planning', findings=findings)
    run_path = project / '.storyloop/state/runs' / (change_id + '.json')
    before = load_json(run_path)
    args = SimpleNamespace(command='status', change_id=change_id)
    status = dispatch(project, args, None, lambda *a: None,
                      lambda p, c: load_json(run_path))
    log = change_log(project, change_id)
    after = load_json(run_path)
    texts = {}
    for name, result in [('run', run), ('status', status)]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            display(result, False)
        texts[name] = output.getvalue()
    texts['log'] = render_log(log)
    other = {}
    for kind in ('DESIGN_APPROVAL_REQUIRED', 'NO_PROGRESS'):
        sample = {'change_id': change_id, 'status': kind, 'attempt': 0,
                  'failures': [], 'findings': findings}
        report = build_stop('/p', sample, kind, {})
        other[kind] = {'stop': report, 'text': '\n'.join(render_stop(report))}
    print(json.dumps({'run': run, 'status': status, 'log': log,
                      'texts': texts, 'before': before, 'after': after,
                      'other': other}, ensure_ascii=False))
'''

BASE_KEYS = {'status', 'routing', 'phase', 'completed', 'last_verdict',
             'attempts', 'blocker', 'evidence', 'facts', 'questions',
             'options', 'resume'}


def probe(findings):
    try:
        child = subprocess.run([sys.executable, '-B', '-c', PROBE],
                               input=json.dumps({'findings': findings}),
                               cwd=Path.cwd(), capture_output=True, text=True,
                               timeout=20)
        if child.returncode:
            raise HarnessError('Business subprocess failed: ' + child.stderr[-1000:])
        result = json.loads(child.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        raise HarnessError('Business subprocess/protocol unavailable') from error
    if not isinstance(result, dict) or set(result) != {
            'run', 'status', 'log', 'texts', 'before', 'after', 'other'}:
        raise HarnessError('Business protocol is invalid')
    precondition(result['run'].get('status') == 'DESIGN_REVISION_REQUIRED',
                 'Run fixture did not reach design revision stop')
    precondition(result['before'].get('findings') == findings,
                 'Saved findings fixture differs from source findings')
    precondition(result['status'].get('stop', {}).get('status') == 'DESIGN_REVISION_REQUIRED',
                 'Status fixture did not read the saved stop')
    precondition(result['log'].get('stop', {}).get('status') == 'DESIGN_REVISION_REQUIRED',
                 'Log fixture did not read the saved stop')
    return result


def findings():
    return [
        {'target': 'design', 'criterion_id': 'AC-1', 'blocking': True,
         'message': '修正数据边界 A', 'location': 'plan:main/constraints/0'},
        {'target': 'design', 'criterion_id': 'AC-1', 'blocking': True,
         'message': '补足回滚路径 B', 'location': 'plan:main/regression/0'},
        {'target': 'design', 'criterion_id': 'AC-1', 'blocking': False,
         'message': '非阻塞建议 C', 'location': 'plan:main/summary'},
    ]


def summaries(stop):
    return stop.get('review_findings', [])


class RedBehavior(unittest.TestCase):
    def test_ac1_run_and_structured_stop(self):
        result = probe(findings())
        stop = result['run']['stop']
        items = summaries(stop)
        self.assertEqual(len(items), 2)
        for item, message, location in zip(items,
                                           ('修正数据边界 A', '补足回滚路径 B'),
                                           ('plan:main/constraints/0', 'plan:main/regression/0')):
            shown = str(item)
            for expected in ('design', '阻塞', message, location):
                self.assertIn(expected, shown)
                self.assertIn(expected, result['texts']['run'])
        self.assertNotIn('非阻塞建议 C', str(items))

    def test_ac2_status_and_log_match_run(self):
        result = probe(findings())
        expected = summaries(result['run']['stop'])
        self.assertEqual(len(expected), 2)
        for consumer in ('status', 'log'):
            self.assertEqual(summaries(result[consumer]['stop']), expected)
            for item, message, location in zip(
                    expected,
                    ('修正数据边界 A', '补足回滚路径 B'),
                    ('plan:main/constraints/0', 'plan:main/regression/0')):
                self.assertEqual(item['target'], 'design')
                self.assertEqual(item['severity'], '阻塞')
                self.assertEqual(item['message'], message)
                self.assertEqual(item['location'], location)
                for value in ('design', '阻塞', message, location, item['summary']):
                    self.assertIn(value, result['texts'][consumer])
            self.assertNotIn('非阻塞建议 C', result['texts'][consumer])

    def test_ac5_redaction_and_length_on_all_surfaces(self):
        secret = 'fixture-secret-73'
        userinfo = 'fixture-user:fixture-pass'
        sensitive = [
            {'target': 'design', 'criterion_id': 'AC-1', 'blocking': True,
             'message': 'token=' + secret + ' 修正边界 ' + '长' * 450,
             'location': 'plan:main/constraints/0'},
            {'target': 'design', 'criterion_id': 'AC-1', 'blocking': True,
             'message': '检查链接 https://' + userinfo + '@example.test/path',
             'location': 'plan:main/' + 'long-location-' * 45},
        ]
        result = probe(sensitive)
        for consumer in ('run', 'status', 'log'):
            items = summaries(result[consumer]['stop'])
            self.assertEqual(len(items), 2)
            rendered = result['texts'][consumer]
            self.assertIn('[REDACTED]', rendered)
            for forbidden in (secret, userinfo, 'RAW_CALL_RECORD_SENTINEL'):
                self.assertNotIn(forbidden, str(items))
                self.assertNotIn(forbidden, rendered)
            for item in items:
                self.assertEqual(item['target'], 'design')
                self.assertEqual(item['severity'], '阻塞')
                self.assertIsInstance(item['message'], str)
                self.assertIsInstance(item['location'], str)
                self.assertIsInstance(item['summary'], str)
                self.assertIn(item['message'], item['summary'])
                self.assertIn(item['location'], item['summary'])
                self.assertIn(item['summary'], rendered)
                self.assertLessEqual(len(item['summary']), 300)
            self.assertIn('token=[REDACTED]', items[0]['message'])
            self.assertIn('plan:main/constraints/0', items[0]['location'])
            self.assertIn('https://[REDACTED]@example.test/path', items[1]['message'])
            self.assertTrue(items[1]['location'].startswith('plan:main/'))


class ExistingBehavior(unittest.TestCase):
    def test_ac3_reads_preserve_saved_state_and_exclude_nonblocking(self):
        source = findings()
        result = probe(source)
        self.assertEqual(result['before'], result['after'])
        self.assertEqual(result['after']['findings'], source)
        self.assertEqual(result['after']['planning']['consistency']['findings'], source)
        for consumer in ('run', 'status', 'log'):
            self.assertNotIn('非阻塞建议 C', result['texts'][consumer])
            self.assertNotIn('非阻塞建议 C', str(result[consumer]['stop']))

    def test_ac4_other_stop_shapes_and_recovery_order(self):
        result = probe(findings())
        for kind in ('DESIGN_APPROVAL_REQUIRED', 'NO_PROGRESS'):
            stop = result['other'][kind]['stop']
            shown = result['other'][kind]['text']
            self.assertEqual(set(stop), BASE_KEYS)
            self.assertEqual(stop['evidence'], [])
            self.assertNotIn('修正数据边界 A', shown)
            self.assertNotIn('补足回滚路径 B', shown)
            self.assertLess(shown.index('阻塞点：'), shown.index('可选操作：'))
            self.assertLess(shown.index('可选操作：'), shown.index('恢复：'))
        approval = result['other']['DESIGN_APPROVAL_REQUIRED']['stop']
        progress = result['other']['NO_PROGRESS']['stop']
        self.assertIn('storyloop approve /p --change FEAT-fixture --design',
                      approval['resume'])
        self.assertEqual(progress['resume'],
                         'storyloop run /p --change FEAT-fixture')
