import json
import subprocess
import sys
import unittest
from pathlib import Path

from storyloop.testing.smoke_harness.v1 import HarnessError, precondition


PROBE = r'''
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from storyloop.models import StoryLoopError
from storyloop.onboarding import scaffold_project
from storyloop.project_loop_guards import read_only_call, require_git_visible, write_safely

request = json.loads(sys.stdin.read())
mode = request['mode']
relative = request.get('path')
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    def git(*args):
        return subprocess.run(['git', *args], cwd=root, capture_output=True,
                              text=True, check=True).stdout.strip()
    git('init', '-q')
    (root / '.gitignore').write_text(
        '*.egg-info/\ntarget/\nrogue/\npkg/cache/\npkg/cache-neighbor/\n',
        encoding='utf-8')
    git('add', '.gitignore')
    git('-c', 'user.name=Acceptance', '-c', 'user.email=acceptance@example.invalid',
        'commit', '-q', '-m', 'fixture')
    fixture = {'head': bool(git('rev-parse', 'HEAD'))}
    if mode == 'draft':
        result = scaffold_project(root, [request['stack']])
        settings = json.loads((root / 'acceptance/storyloop-settings.json').read_text(encoding='utf-8'))
        outcome = {'status': result['status'], 'scratch_paths': settings.get('scratch_paths')}
    else:
        scratch = request['scratch']
        control = root / '.storyloop/config.json'
        control.parent.mkdir()
        control.write_text(json.dumps({'profile': {'scratch_paths': scratch}}), encoding='utf-8')
        before = json.loads(control.read_text(encoding='utf-8'))['profile']['scratch_paths']
        target = root / relative
        fixture['configured'] = before == scratch
        fixture['absent_before'] = not target.exists()
        if relative.startswith('.storyloop/'):
            fixture['ignored'] = True
        else:
            fixture['ignored'] = subprocess.run(['git', 'check-ignore', '-q', relative],
                                                cwd=root).returncode == 0
        def operation():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('generated\n', encoding='utf-8')
            return {'summary': 'done'}
        try:
            if mode == 'writer':
                _, changes = write_safely(root, 'FIX-fixture', operation)
                require_git_visible(changes, {'allowed': [], 'violations': []})
            else:
                read_only_call(root, 'FIX-fixture', operation)
            outcome = {'status': 'PASSED'}
        except StoryLoopError as error:
            outcome = {'status': error.code}
        outcome['scratch_before'] = before
        outcome['scratch_after'] = json.loads(control.read_text(encoding='utf-8'))['profile']['scratch_paths']
    print(json.dumps({'fixture': fixture, 'outcome': outcome}))
'''


def probe(request):
    try:
        child = subprocess.run([sys.executable, '-B', '-c', PROBE],
                               input=json.dumps(request), cwd=Path.cwd(),
                               capture_output=True, text=True, timeout=20)
        if child.returncode:
            raise HarnessError('Business subprocess failed: ' + child.stderr[-1000:])
        payload = json.loads(child.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        raise HarnessError('Business subprocess/protocol unavailable') from error
    if not isinstance(payload, dict) or set(payload) != {'fixture', 'outcome'}:
        raise HarnessError('Business protocol is invalid')
    fixture, outcome = payload['fixture'], payload['outcome']
    if not isinstance(fixture, dict) or not isinstance(outcome, dict) or 'status' not in outcome:
        raise HarnessError('Business protocol is invalid')
    precondition(fixture.get('head') is True, 'Temporary Git fixture has no committed HEAD')
    if request['mode'] != 'draft':
        precondition(fixture.get('configured') is True, 'Scratch configuration fixture is invalid')
        precondition(fixture.get('absent_before') is True, 'Generated path already exists')
        precondition(fixture.get('ignored') is True, 'Generated output is not Git-ignored')
    return outcome


class RedBehavior(unittest.TestCase):
    def test_ac1_writer_accepts_root_and_nested_glob(self):
        for relative in ('storyloop_engine.egg-info/PKG-INFO',
                         'pkg/another_package.egg-info/PKG-INFO'):
            with self.subTest(path=relative):
                result = probe({'mode': 'writer', 'scratch': ['*.egg-info'], 'path': relative})
                self.assertEqual(result['status'], 'PASSED')

    def test_ac2_reader_accepts_declared_glob(self):
        result = probe({'mode': 'reader', 'scratch': ['*.egg-info'],
                        'path': 'pkg/read_only.egg-info/PKG-INFO'})
        self.assertEqual(result['status'], 'PASSED')

    def test_ac7_python_draft_has_all_seven_defaults(self):
        result = probe({'mode': 'draft', 'stack': 'python'})
        self.assertEqual(result['status'], 'SCAFFOLD_CREATED')
        self.assertEqual(result['scratch_paths'], [
            '__pycache__', '.pytest_cache', '.ruff_cache', '.mypy_cache',
            '*.egg-info', 'build', 'dist'])


class ExistingBehavior(unittest.TestCase):
    def test_ac3_exact_name_still_matches_nested_directory(self):
        result = probe({'mode': 'writer', 'scratch': ['target'],
                        'path': 'pkg/target/App.class'})
        self.assertEqual(result['status'], 'PASSED')

    def test_ac3_relative_path_keeps_prefix_boundary(self):
        declared = ['pkg/cache']
        result = probe({'mode': 'writer', 'scratch': declared,
                        'path': 'pkg/cache/deep/output.bin'})
        self.assertEqual(result['status'], 'PASSED')
        neighbor = probe({'mode': 'writer', 'scratch': declared,
                          'path': 'pkg/cache-neighbor/output.bin'})
        self.assertEqual(neighbor['status'], 'WORKSPACE_TAINTED')

    def test_ac4_undeclared_ignored_output_is_tainted(self):
        for declared, relative in (([], 'rogue/App.class'),
                                   (['*.whl', 'target'], 'foo.egg-info/PKG-INFO')):
            with self.subTest(scratch=declared):
                result = probe({'mode': 'writer', 'scratch': declared, 'path': relative})
                self.assertEqual(result['status'], 'WORKSPACE_TAINTED')

    def test_ac5_control_plane_remains_protected(self):
        result = probe({'mode': 'reader', 'scratch': ['drafts'],
                        'path': '.storyloop/drafts/unlisted.json'})
        self.assertEqual(result['status'], 'WORKSPACE_TAINTED')

    def test_ac6_persisted_scratch_value_is_unchanged(self):
        declared = ['target', 'build/output']
        result = probe({'mode': 'writer', 'scratch': declared,
                        'path': 'pkg/target/App.class'})
        self.assertEqual(result['status'], 'PASSED')
        self.assertEqual(result['scratch_before'], declared)
        self.assertEqual(result['scratch_after'], result['scratch_before'])

    def test_ac8_non_python_draft_has_no_python_build_defaults(self):
        result = probe({'mode': 'draft', 'stack': 'node'})
        self.assertEqual(result['status'], 'SCAFFOLD_CREATED')
        self.assertEqual(result['scratch_paths'], [])


if __name__ == '__main__':
    unittest.main()
