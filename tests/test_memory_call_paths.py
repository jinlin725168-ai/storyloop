"""Memory consumers share call storage while retaining their path safety gates."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.call_log import call_folder
from storyloop.errata import _meta_path as errata_path
from storyloop.retrospective import _meta_path as retrospective_path
from storyloop.report_memory import _complete_meta
from storyloop.models import StoryLoopError
from storyloop.storage import write_json


class MemoryCallPathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name).resolve()

    def test_all_consumers_follow_the_shared_call_storage_location(self):
        with patch('storyloop.call_log.CALLS_ROOT', Path('.storyloop/state/telemetry/alternate-calls')):
            expected = call_folder(self.project, 'call-1') / 'meta.json'
            evidence = dict(call='call-1', lessons_provided=['L-0001'],
                            lessons_claimed=[], lessons_unknown=[])
            write_json(expected, evidence)
            for consumer in (errata_path, retrospective_path):
                with self.subTest(consumer=consumer.__module__):
                    self.assertEqual(consumer(self.project, 'call-1'), expected)
            self.assertEqual(_complete_meta(self.project, 'call-1'), evidence)

    def test_invalid_ids_remain_rejected(self):
        for call in ('../escape', '/outside', '', 'call/child'):
            for consumer in (errata_path, retrospective_path):
                with self.subTest(call=call, consumer=consumer.__module__):
                    with self.assertRaises(StoryLoopError):
                        consumer(self.project, call)
            self.assertIsNone(_complete_meta(self.project, call))

    def test_symlinked_call_root_remains_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            root = call_folder(self.project, 'call-1').parent
            root.parent.mkdir(parents=True)
            root.symlink_to(outside, target_is_directory=True)
            for consumer in (errata_path, retrospective_path):
                with self.assertRaises(StoryLoopError):
                    consumer(self.project, 'call-1')
            self.assertIsNone(_complete_meta(self.project, 'call-1'))
