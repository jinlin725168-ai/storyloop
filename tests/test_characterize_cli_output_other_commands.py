"""Existing text output for commands outside readiness."""

import io
import unittest
from contextlib import redirect_stdout

from storyloop.cli_output import display


class OtherCommandOutputCharacterization(unittest.TestCase):
    def test_regular_report_keeps_status_and_existing_fields(self):
        stream = io.StringIO()
        with redirect_stdout(stream):
            display({'status': 'REPORT', 'change_id': 'CHANGE-1',
                     'warnings': ['review needed']}, complete_json=False)

        lines = stream.getvalue().splitlines()
        self.assertIn('REPORT', lines)
        self.assertIn('change_id: CHANGE-1', lines)
        self.assertIn('warning: review needed', lines)


if __name__ == '__main__':
    unittest.main()
