"""Checked-out files are compared with commits the way Git sees them (core.autocrlf is the
Git for Windows default), not byte for byte against the stored blob."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storyloop.principles import load_principles
from storyloop.storage import load_json
from tests.routing_support import routing_repository
from tests.test_baseline_behavior import git


def _check_out_with_crlf(root: Path, relative: str) -> None:
    git(root, 'config', 'core.autocrlf', 'true')
    (root / relative).unlink()
    git(root, 'checkout', '--', relative)
    assert b'\r\n' in (root / relative).read_bytes()


class AutocrlfTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        routing_repository(self.root)

    def test_principles_checked_out_with_crlf_are_unchanged(self):
        path = self.root / 'openspec/principles.json'
        path.parent.mkdir(exist_ok=True)
        path.write_bytes((json.dumps({'version': 1, 'principles': [
            {'id': 'P1', 'text': 'Preserve module dependency direction'}]}, indent=2) + '\n').encode())
        git(self.root, 'add', 'openspec/principles.json')
        git(self.root, 'commit', '-qm', 'principles')
        source = git(self.root, 'rev-parse', 'HEAD').strip()
        _check_out_with_crlf(self.root, 'openspec/principles.json')
        with patch('storyloop.principles.load_baseline', return_value={'source_head': source}):
            self.assertEqual([p['id'] for p in load_principles(self.root)['principles']], ['P1'])

    def test_restore_accepts_a_crlf_checkout_of_the_contract_blob(self):
        from storyloop.routing_restore import prepare_restore, resume_restore
        _check_out_with_crlf(self.root, 'app/service.py')
        state = {'change_id': 'FEAT-crlf', 'attempt': 2,
                 'contract_commit': git(self.root, 'rev-parse', 'HEAD').strip(),
                 'planning': {'origin': 'L3', 'phase': 'REPLAN', 'consumed_attempt': 2}}
        (self.root / 'app/service.py').write_text('candidate = 1\n')
        prepare_restore(self.root, state, ['app/service.py'])
        saved = load_json(self.root / '.storyloop/state/runs/FEAT-crlf.json')
        resume_restore(self.root, saved)
        self.assertEqual(git(self.root, 'status', '--porcelain', '--', 'app/service.py'), '')


if __name__ == '__main__':
    unittest.main()
