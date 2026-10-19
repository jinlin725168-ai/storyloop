import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from storyloop.base_upgrade import propose_base_change, upgrade_baseline
from storyloop.baseline import bootstrap_project
from storyloop.models import StoryLoopError
from storyloop.storage import load_json
from tests.test_baseline_behavior import git, repository


class OracleSkillApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        repository(self.root)
        self.path = '.claude/skills/local/SKILL.md'
        target = self.root / self.path
        target.parent.mkdir(parents=True)
        target.write_text('---\nroles: [review]\n---\nold', encoding='utf-8')
        git(self.root, 'add', self.path)
        git(self.root, 'commit', '-qm', 'skill')
        bootstrap_project(self.root, {'test_commands': [[sys.executable, '-c', 'pass']]})
        target.write_text('---\nroles: [design]\n---\nchanged role and body', encoding='utf-8')
        self.proposal = propose_base_change(self.root, [self.path], 'skill change')['change_id']
        git(self.root, 'add', self.path)
        git(self.root, 'commit', '-qm', 'change skill')

    def test_oracle_role_removal_still_requires_confirmation(self):
        with self.assertRaisesRegex(StoryLoopError, 'confirm-oracle-skills'):
            upgrade_baseline(self.root, self.proposal)

    def test_confirmed_receipt_recovers_without_extending_approval(self):
        from storyloop.oracle_skill_approval import confirmed_upgrade
        with patch('storyloop.base_upgrade._regression_layers', side_effect=RuntimeError('crash')):
            with self.assertRaisesRegex(RuntimeError, 'crash'):
                confirmed_upgrade(self.root, self.proposal, True)
        receipt = load_json(self.root / '.storyloop/state/base-upgrades' / (self.proposal + '.json'))
        approval = receipt['oracle_skills_approval']
        self.assertEqual(approval['source_head'], receipt['source_head'])
        self.assertEqual(approval['proposal_digest'], receipt['proposal_digest'])
        with patch('storyloop.base_upgrade._regression_layers', return_value={'status': 'PASSED'}):
            self.assertEqual(upgrade_baseline(self.root, self.proposal)['status'], 'BASELINE_UPGRADED')
        # Confirmation scope is reset even when the confirmed call crashed.
        from storyloop.oracle_skill_approval import CONFIRMED
        self.assertFalse(CONFIRMED.get())
