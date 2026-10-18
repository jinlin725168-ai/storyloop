"""Supersession preserves evidence and explicitly reassesses on the source branch."""
from dataclasses import replace
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch

from storyloop.approvals import approve_change
from storyloop.changes import load_change, revise_change
from storyloop.cli import dispatch, parser
from storyloop.design_approval import approve_design
from storyloop.models import StoryLoopError
from storyloop.project_loop import load_run_state, run_change
from storyloop.supersede import supersede_change
from tests.routing_support import routed_acceptance, routed_case, routed_product
from tests.test_baseline_behavior import git
from tests.test_routing_planning import answer
from tests.test_supersede import contract_stop, revised


class RoutedSupersedeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def stopped(self, with_lock=False):
        change, services = routed_case(self.root, 'heavy')
        identifier = change['change_id']
        if with_lock:
            value = routed_product()
            value['assessment']['scope']['creates'].append('tests/test_lock.py')
            revise_change(self.root, identifier, value, 'Declare characterization test')
            change = approve_change(self.root, identifier, tier='heavy')
            services = replace(services, design=lambda p, c: answer(value),
                               implement=lambda p, c: {
                                   'summary': 'Contract needs clarification',
                                   'questions': ['Which version is required?']})
        pause = run_change(self.root, identifier, services)
        self.assertEqual(pause['status'], 'DESIGN_APPROVAL_REQUIRED')
        if with_lock:
            approve_design(self.root, identifier, pause['plan_id'])
            result = run_change(self.root, identifier, services)
            self.assertEqual(result['status'], 'NEEDS_INPUT', result)
            state = load_run_state(self.root, identifier)
            self.assertEqual(state['characterization']['status'], 'GREEN')
            self.assertTrue(git(self.root, 'show', 'HEAD:tests/test_lock.py'))
        return change, services

    def supersede(self, identifier):
        return supersede_change(self.root, identifier, 'Clarify version',
                                lambda c: routed_product(), reason='Contract needs clarification')

    def test_next_reassesses_on_source_then_approves_and_enters_new_run(self):
        old, services = self.stopped()
        old_head = git(self.root, 'rev-parse', 'HEAD')
        candidate = (self.root / 'app/service.py').read_text() + '# retained old candidate\n'
        (self.root / 'app/service.py').write_text(candidate)
        with patch('storyloop.cli.invoke_project_agent', return_value=routed_product()):
            new = dispatch(parser().parse_args([
                'change', str(self.root), '--supersede', old['change_id'],
                '--message', 'Clarify version', '--reason', 'Contract needs clarification']))
        source = new['supersedes']['source_branch']
        revision = self.revision_command(new['next'])
        self.assertIsNone(new['approval'])
        self.assertEqual(self.revision_command(self.status(new)['next']), revision)
        self.assertEqual((self.root / 'app/service.py').read_text(), candidate)
        git(self.root, 'stash', 'push', '-m', 'preserved superseded candidate')
        approve_change(self.root, new['change_id'])
        with self.assertRaisesRegex(StoryLoopError, 'git checkout'):
            run_change(self.root, new['change_id'], services)
        checkout = next(shlex.split(line) for line in new['next'].splitlines() if line.startswith('git '))
        git(self.root, *checkout[1:])
        source_head = git(self.root, 'rev-parse', 'HEAD')
        with self.assertRaisesRegex(StoryLoopError, 'intake evidence is stale'):
            approve_change(self.root, new['change_id'])
        self.assertFalse((self.root / '.storyloop/state/runs' / f"{new['change_id']}.json").exists())
        seen = []
        with patch('storyloop.cli.invoke_project_agent', side_effect=lambda p, r, c:
                   seen.append((r, c['source_commit'])) or routed_product()):
            revised_draft = dispatch(parser().parse_args(revision[1:]))
        self.assertEqual(seen, [('product', source_head)])
        self.assertEqual(revised_draft['intake']['source_commit'], source_head)
        self.assertIsNone(revised_draft['approval'])
        self.assertEqual(revised_draft['revision'], 2)
        self.assertEqual(revised_draft['supersedes'], new['supersedes'])
        new_services = replace(services, generate_smoke=lambda p, c: routed_acceptance(new['change_id']))
        with patch('storyloop.cli.run_with_answers', side_effect=lambda p, c, **kw:
                   run_change(p, c, new_services)):
            result = dispatch(parser().parse_args([
                'approve', str(self.root), '--change', new['change_id'], '--tier', 'heavy']))
        self.assertEqual(result['status'], 'DESIGN_APPROVAL_REQUIRED', result)
        self.assertEqual(load_run_state(self.root, new['change_id'])['source_branch'], source)
        self.assertEqual(git(self.root, 'rev-parse', f"feature/{old['change_id']}"), old_head)
        self.assertEqual(git(self.root, 'show', 'stash@{0}:app/service.py'), candidate.strip())
        self.assertEqual(load_change(self.root, old['change_id'])['approval'], old['approval'])

    def status(self, change):
        return dispatch(parser().parse_args([
            'status', str(self.root), '--change', change['change_id']]))

    def revision_command(self, hint):
        commands = [shlex.split(line) for line in hint.splitlines()
                    if line.startswith('storyloop change ')]
        self.assertEqual(len(commands), 1, hint)
        return commands[0]

    def test_committed_characterization_creates_does_not_reassess_old_contract(self):
        old, _ = self.stopped(with_lock=True)
        new = self.supersede(old['change_id'])
        saved = load_change(self.root, old['change_id'])
        self.assertEqual(saved['status'], 'SUPERSEDED')
        self.assertEqual(saved['intake'], old['intake'])
        self.assertEqual(saved['approval'], old['approval'])
        self.assertEqual(load_run_state(self.root, old['change_id'])['status'], 'SUPERSEDED')
        git(self.root, 'checkout', new['supersedes']['source_branch'])
        again = self.supersede(old['change_id'])
        self.assertEqual(again['change_id'], new['change_id'])
        self.assertEqual(again['intake'], new['intake'])
        self.assertEqual(again['approval'], new['approval'])

    def test_partial_child_save_retry_preserves_evidence_across_checkout(self):
        old, _ = self.stopped(with_lock=True)
        with patch('storyloop.changes.write_change_preview', side_effect=OSError('disk interrupted')):
            with self.assertRaisesRegex(OSError, 'disk interrupted'):
                self.supersede(old['change_id'])
        children = [p.stem for p in (self.root / '.storyloop/state/changes').glob('*.json')
                    if p.stem != old['change_id']]
        self.assertEqual(len(children), 1)
        new = load_change(self.root, children[0])
        git(self.root, 'checkout', new['supersedes']['source_branch'])
        recovered = self.supersede(old['change_id'])
        self.assertEqual(recovered['intake'], new['intake'])
        self.assertEqual(recovered['approval'], new['approval'])
        self.assertEqual(load_change(self.root, old['change_id'])['intake'], old['intake'])
        self.assertEqual(load_change(self.root, old['change_id'])['approval'], old['approval'])
        self.assertEqual(load_run_state(self.root, old['change_id'])['status'], 'SUPERSEDED')
        self.assertTrue((self.root / '.storyloop/drafts' / children[0] / 'proposal.md').is_file())

    def test_retry_preserves_approved_child_evidence_at_different_head(self):
        old, _ = self.stopped()
        new = self.supersede(old['change_id'])
        git(self.root, 'checkout', new['supersedes']['source_branch'])
        revised_draft = revise_change(self.root, new['change_id'], routed_product(), 'Reassess source')
        approved = approve_change(self.root, new['change_id'])
        git(self.root, 'checkout', f"feature/{old['change_id']}")
        again = self.supersede(old['change_id'])
        self.assertEqual(again['intake'], revised_draft['intake'])
        self.assertEqual(again['approval'], approved['approval'])
        self.assertEqual(load_change(self.root, new['change_id'])['intake'], approved['intake'])

    def test_legacy_supersession_does_not_add_intake_to_old_contract(self):
        identifier = contract_stop(self.root)
        old = load_change(self.root, identifier)
        self.assertNotIn('intake', old)
        supersede_change(self.root, identifier, 'Revise criterion', revised)
        saved = load_change(self.root, identifier)
        self.assertNotIn('intake', saved)
        self.assertNotIn('routing_version', saved)
        self.assertEqual(saved['approval'], old['approval'])


if __name__ == '__main__':
    unittest.main()
