"""Existing failed-command output tail contract for readiness collection."""

import tempfile
import unittest
from pathlib import Path

from storyloop.smoke_runner import _run_test


class SmokeRunnerTailCharacterization(unittest.TestCase):
    def test_failed_command_keeps_bounded_redacted_stream_tails(self):
        script = ("import sys; "
                  "sys.stdout.write('a'*1000 + ' OUTTAIL'); "
                  "sys.stderr.write('b'*1000 + ' token=synthetic-secret TAIL'); "
                  "sys.exit(1)")
        with tempfile.TemporaryDirectory() as folder:
            result = _run_test(Path(folder), {
                'id': 'tail', 'criteria': [],
                'command': ['{python}', '-c', script], 'timeout_seconds': 10,
            }, capture_output_tail=True)

        self.assertEqual(result['status'], 'FAIL')
        tail = result['output_tail']
        self.assertIn('OUTTAIL', tail['stdout'])
        self.assertIn('TAIL', tail['stderr'])
        self.assertIn('[REDACTED]', tail['stderr'])
        self.assertNotIn('synthetic-secret', str(tail))
        self.assertLessEqual(len(tail['stdout']) + len(tail['stderr']), 1500)


if __name__ == '__main__':
    unittest.main()
