"""Existing readiness status and successful command result contract."""

import tempfile
import unittest
from pathlib import Path

from storyloop.readiness import inspect_readiness
from storyloop.storage import load_json, write_json
from tests.routing_support import readiness_repository


class ReadinessProjectionCharacterization(unittest.TestCase):
    def test_failed_check_blocks_ready_while_passed_result_has_no_failure_output(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            readiness_repository(project)
            config_path = project / '.storyloop/config.json'
            config = load_json(config_path)
            config['profile']['check_commands'] = [
                ['{python}', '-c', 'raise SystemExit(1)'],
                ['{python}', '-c', "print('passed')"],
            ]
            write_json(config_path, config)
            record = inspect_readiness(project)

        self.assertEqual(record['status'], 'PROJECT_NOT_READY')
        self.assertIn('checks', record['blockers'])
        results = record['layers']['checks']['results']
        self.assertEqual(results[0]['status'], 'FAIL')
        self.assertIn('output_tail', results[0])
        self.assertEqual(results[1]['status'], 'PASS')
        self.assertNotIn('output_tail', results[1])
        self.assertNotIn('failure_summary', results[1])


if __name__ == '__main__':
    unittest.main()
