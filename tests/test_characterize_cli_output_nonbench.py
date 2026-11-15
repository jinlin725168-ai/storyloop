"""Other CLI result text remains readable when bench report gets its own renderer."""

import io
from contextlib import redirect_stdout
import unittest

from storyloop.cli_output import display


class NonBenchOutputCharacterization(unittest.TestCase):
    def test_status_report_keeps_existing_fields_and_warning(self):
        result = {"status": "REPORT", "change_id": "CHANGE1", "project": "demo",
                  "warnings": ["review needed"]}
        output = io.StringIO()

        with redirect_stdout(output):
            display(result, complete_json=False)

        lines = output.getvalue().splitlines()
        self.assertIn("REPORT", lines)
        self.assertIn("change_id: CHANGE1", lines)
        self.assertIn("project: demo", lines)
        self.assertIn("warning: review needed", lines)


if __name__ == "__main__":
    unittest.main()
