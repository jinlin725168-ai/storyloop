"""A public run on an existing BASE stop must return it without an agent call."""
import json
from pathlib import Path
import shlex
import subprocess
import sys
import unittest


PROBE = r'''
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project, load_project_config
from storyloop.project_loop import LoopServices, run_change
from storyloop.stop_report import stop_of
from storyloop.storage import write_json
from tests.routing_support import legacy_create_change
from tests.support import product
from tests.test_baseline_behavior import git, repository
from tests.test_project_loop import smoke_payload

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder, 'project with space')
    root.mkdir()
    repository(root)
    (root / 'pom.xml').write_text('<project>old</project>\n')
    for name in ('tests/test_changed.py', 'tests/test_removed.py'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('VALUE = 1\n')
    git(root, 'add', 'pom.xml', 'tests')
    git(root, 'commit', '-m', 'foundation')
    bootstrap_project(root, {'test_commands': [], 'test_roots': ['tests']})
    config = load_project_config(root)
    config['max_attempts'] = 3
    write_json(root / '.storyloop/config.json', config)
    change_id = legacy_create_change(root, 'orders', product())['change_id']
    approve_change(root, change_id)

    def implement(project, context):
        if context['attempt'] == 1:
            target = project / 'modules/orders/service.py'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('VALUE = 2\n')
            (project / 'tests/test_changed.py').write_text('VALUE = 2\n')
            (project / 'tests/test_removed.py').unlink()
        else:
            (project / 'pom.xml').write_text('<project>new</project>\n')
        return {'summary': 'candidate', 'questions': []}

    stopped = run_change(root, change_id, LoopServices(
        generate_smoke=lambda p, c: smoke_payload(change_id), implement=implement,
        review=lambda p, c: {'verdict': 'fail', 'summary': 'retry',
                             'findings': [], 'questions': []}))
    report_file = root / 'reports/storyloop' / change_id / 'report.md'
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(stop_of(root, json.loads((root / '.storyloop/state/runs'
                                                    / (change_id + '.json')).read_text())
                                   )['options'][0]['action'])

    def public(command, extra=(), env=None):
        child = subprocess.run([sys.executable, '-m', 'storyloop', command, str(root),
                                '--change', change_id, '--json', *extra],
                               cwd=Path.cwd(), capture_output=True, text=True,
                               env=env, timeout=30)
        if not child.stdout.strip():
            raise RuntimeError(command + ' CLI failed: ' + child.stderr[-1000:])
        return json.loads(child.stdout)

    status = public('status')
    log = public('log')
    report = public('report')
    stub_dir = Path(folder, 'agent-stubs')
    stub_dir.mkdir()
    marker = stub_dir / 'called'
    for name in ('codex', 'claude'):
        stub = stub_dir / name
        stub.write_text('#!/bin/sh\necho called > ' + str(marker) + '\nexit 91\n')
        stub.chmod(0o755)
    repeated = public('run', ('--no-interactive',),
                      {**os.environ, 'PATH': str(stub_dir) + os.pathsep
                       + os.environ.get('PATH', '')})
    print(json.dumps({'original_status': stopped['status'],
                      'repeated_status': repeated['status'],
                      'original_options': stopped['stop']['options'],
                      'repeated_options': repeated['stop']['options'],
                      'original_next': stopped['next'],
                      'repeated_next': repeated.get('next'),
                      'root': str(root),
                      'actions': [stopped['stop']['options'][0]['action'],
                                  repeated['stop']['options'][0]['action'],
                                  status['stop']['options'][0]['action'],
                                  log['stop']['options'][0]['action'],
                                  Path(report['report']).read_text()],
                      'agent_stub_called': marker.exists()}))
'''


class PublicRunStop(unittest.TestCase):
    def probe(self):
        child = subprocess.run([sys.executable, '-c', PROBE], cwd=Path.cwd(),
                               capture_output=True, text=True, timeout=90)
        self.assertEqual(child.returncode, 0, child.stderr[-1500:])
        return json.loads(child.stdout)

    def test_ac2_public_views(self):
        value = self.probe()
        prefix = 'git -C ' + shlex.quote(value['root'])
        for action in value['actions']:
            self.assertIn(prefix, action)
            self.assertIn('--literal-pathspecs stash push -u --', action)
        self.assertEqual(len(set(value['actions'])), 1)

    def test_repeat_base_stop_without_agent(self):
        value = self.probe()
        self.assertEqual(value['original_status'], 'BASE_CHANGE_REQUIRED')
        self.assertEqual(value['repeated_status'], 'BASE_CHANGE_REQUIRED')
        self.assertEqual(value['repeated_options'], value['original_options'])
        self.assertEqual(value['repeated_next'], value['original_next'])
        self.assertFalse(value['agent_stub_called'])


if __name__ == '__main__':
    unittest.main()
