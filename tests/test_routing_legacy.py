"""Historical states retain their bytes and protocol; new drafts never downgrade."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from storyloop.approvals import approve_change
from storyloop.changes import create_change, contract_digest
from storyloop.models import StoryLoopError
from storyloop.project_loop import LoopServices, load_run_state, run_change
from tests.routing_support import legacy_create_change, readiness_repository, routed_implement
from tests.support import product
from tests.test_project_loop import smoke_payload
from tests.test_structured_output import passing_review


class LegacyCompatibilityTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        readiness_repository(self.root, module_roots=['app', 'modules'])

    def artifacts(self, identifier):
        folder = self.root / '.storyloop/drafts' / identifier
        return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob('*') if p.is_file()}

    def test_draft_approved_running_and_passed_keep_historical_contract(self):
        change = legacy_create_change(self.root, 'historical orders', product())
        identifier = change['change_id']
        original = self.artifacts(identifier)
        self.assertIn('stories.json', original)
        self.assertIn('acceptance.json', original)
        digest = contract_digest(change['product'])
        self.assertEqual(change['status'], 'DRAFT')
        approved = approve_change(self.root, identifier)
        self.assertEqual(approved['approval']['contract_digest'], digest)
        self.assertNotIn('intake_digest', approved['approval'])
        self.assertEqual(self.artifacts(identifier), original)
        implement = Mock(return_value={'summary': 'No change yet', 'questions': []})
        services = LoopServices(lambda p, c: smoke_payload(identifier), implement, passing_review)
        stopped = run_change(self.root, identifier, services)
        self.assertEqual(stopped['status'], 'NO_PROGRESS')
        before = load_run_state(self.root, identifier)
        self.assertNotIn('routing', before)
        self.assertGreater(before['attempt'], 0)
        from storyloop.storage import load_json, write_json
        config_path = self.root / '.storyloop/config.json'
        config = load_json(config_path)
        config['max_attempts'] = before['attempt'] + 1
        write_json(config_path, config)
        implement = Mock(wraps=routed_implement)
        result = run_change(self.root, identifier, replace(services, implement=implement))
        self.assertEqual(result['status'], 'PASSED', result)
        self.assertEqual(implement.call_count, 1)
        after = load_run_state(self.root, identifier)
        self.assertNotIn('routing', after)
        for path, content in original.items():
            self.assertEqual(self.artifacts(identifier)[path], content)
            # A core.autocrlf checkout differs from the blob only in line endings.
            checkout = (self.root / 'openspec/changes' / identifier / path).read_bytes()
            self.assertEqual(checkout.replace(b'\r\n', b'\n'), content)
        self.assertEqual(run_change(self.root, identifier, services)['status'], 'PASSED')
        self.assertEqual(load_run_state(self.root, identifier), after)

    def test_identical_missing_assessment_is_blocked_on_new_default(self):
        change = create_change(self.root, 'new orders', product())
        self.assertEqual(change['routing_version'], 1)
        self.assertEqual(change['intake']['status'], 'NEEDS_INPUT')
        with self.assertRaises(StoryLoopError):
            approve_change(self.root, change['change_id'])
        with self.assertRaises(StoryLoopError):
            run_change(self.root, change['change_id'])
        self.assertFalse((self.root / '.storyloop/state/runs' / (change['change_id'] + '.json')).exists())
