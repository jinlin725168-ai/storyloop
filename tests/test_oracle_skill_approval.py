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


class OracleSkillPrecedenceTests(unittest.TestCase):
    def candidate(self, before, after):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        repository(root)
        for relative, body in before.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding='utf-8')
        if before:
            git(root, 'add', *before)
            git(root, 'commit', '-qm', 'initial skills')
        bootstrap_project(root, {'test_commands': [[sys.executable, '-c', 'pass']]})
        changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
        for relative in changed:
            path = root / relative
            if relative in after:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(after[relative], encoding='utf-8')
            else:
                path.unlink()
        proposal = propose_base_change(root, changed, 'skill precedence')['change_id']
        git(root, 'add', '-A', '--', *changed)
        git(root, 'commit', '-qm', 'switch effective skill')
        return root, proposal

    def require_explicit_upgrade(self, before, after):
        from storyloop.oracle_skill_approval import confirmed_upgrade
        root, proposal = self.candidate(before, after)
        with self.assertRaisesRegex(StoryLoopError, 'confirm-oracle-skills'):
            upgrade_baseline(root, proposal)
        self.assertFalse((root / '.storyloop/state/base-upgrades' / (proposal + '.json')).exists())
        self.assertEqual(confirmed_upgrade(root, proposal, True)['status'], 'BASELINE_UPGRADED')
        self.assertEqual(upgrade_baseline(root, proposal)['status'], 'BASELINE_UPGRADED')
        return root, proposal

    def test_codex_oracle_shadowed_by_new_claude_design_copy(self):
        old = {'.codex/skills/custom/SKILL.md': '---\nroles: [review]\n---\noracle'}
        new = {**old, '.claude/skills/custom/SKILL.md': '---\nroles: [design]\n---\nshadow'}
        self.require_explicit_upgrade(old, new)

    def test_packaged_oracle_shadowed_by_new_project_design_copy(self):
        self.require_explicit_upgrade({}, {'.claude/skills/oracle-craft/SKILL.md':
            '---\nroles: [design]\n---\nshadow'})

    def test_removing_shadow_restores_codex_or_packaged_oracle(self):
        for name, lower in [('custom', {'.codex/skills/custom/SKILL.md':
                '---\nroles: [review]\n---\noracle'}), ('oracle-craft', {})]:
            with self.subTest(name=name):
                old = {**lower, f'.claude/skills/{name}/SKILL.md': '---\nroles: [design]\n---\nshadow'}
                self.require_explicit_upgrade(old, lower)

    def test_deleting_only_project_oracle_requires_confirmation(self):
        self.require_explicit_upgrade({'.codex/skills/custom/SKILL.md':
            '---\nroles: [acceptance]\n---\noracle'}, {})

    def test_lower_priority_copy_changes_do_not_require_confirmation(self):
        selected = '.claude/skills/custom/SKILL.md'
        shadowed = '.codex/skills/custom/SKILL.md'
        old = {selected: '---\nroles: [review]\n---\nselected',
               shadowed: '---\nroles: [review]\n---\nshadowed'}
        root, proposal = self.candidate(old, {**old, shadowed: '---\nroles: [design]\n---\nchanged'})
        self.assertEqual(upgrade_baseline(root, proposal)['status'], 'BASELINE_UPGRADED')

    def test_selected_reference_change_requires_confirmation(self):
        old = {'.codex/skills/custom/SKILL.md': '---\nroles: [review]\n---\noracle',
               '.codex/skills/custom/references/rules.md': 'old oracle assertions'}
        self.require_explicit_upgrade(old, {**old, '.codex/skills/custom/references/rules.md': 'new assertions'})

    def test_higher_copy_stack_filter_cannot_remove_oracle_without_confirmation(self):
        old = {'.codex/skills/custom/SKILL.md': '---\nroles: [review]\n---\noracle'}
        new = {**old, '.claude/skills/custom/SKILL.md': '---\nroles: [review]\nstacks: [java]\n---\nfiltered'}
        self.require_explicit_upgrade(old, new)

    def test_priority_switch_confirmation_survives_crash_for_same_candidate_only(self):
        from storyloop.oracle_skill_approval import confirmed_upgrade, CONFIRMED
        old = {'.codex/skills/custom/SKILL.md': '---\nroles: [review]\n---\noracle'}
        new = {**old, '.claude/skills/custom/SKILL.md': '---\nroles: [design]\n---\nshadow'}
        root, proposal = self.candidate(old, new)
        with patch('storyloop.base_upgrade._regression_layers', side_effect=RuntimeError('crash')):
            with self.assertRaisesRegex(RuntimeError, 'crash'):
                confirmed_upgrade(root, proposal, True)
        self.assertFalse(CONFIRMED.get())
        receipt_path = root / '.storyloop/state/base-upgrades' / (proposal + '.json')
        receipt = load_json(receipt_path)
        self.assertEqual(receipt['oracle_skills_approval']['source_head'], receipt['source_head'])
        self.assertEqual(receipt['oracle_skills_approval']['proposal_digest'], receipt['proposal_digest'])
        from storyloop.storage import write_json
        stale = {**receipt, 'oracle_skills_approval': {**receipt['oracle_skills_approval'], 'source_head': '0' * 40}}
        write_json(receipt_path, stale)
        with self.assertRaisesRegex(StoryLoopError, 'confirm-oracle-skills'):
            upgrade_baseline(root, proposal)
        write_json(receipt_path, receipt)
        self.assertEqual(upgrade_baseline(root, proposal)['status'], 'BASELINE_UPGRADED')
        self.assertEqual(load_json(receipt_path)['oracle_skills_approval'], receipt['oracle_skills_approval'])
