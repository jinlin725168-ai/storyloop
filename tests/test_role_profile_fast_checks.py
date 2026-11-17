"""Writers may run the project's own unit commands by default (2026-11-17 L3).

On sqlite-utils the Claude characterize role reported "Bash was denied" and could not run
pytest: profile permissions allow only fast_checks, which defaulted to check_commands alone,
and the project had no static check configured.
"""

import unittest

from storyloop.role_profiles import _fast_checks


class FastCheckDefaultTests(unittest.TestCase):
    def test_writers_default_to_check_and_unit_commands(self):
        unit = ["{python}", "-m", "pytest", "-q", "tests"]
        lint = ["{python}", "-m", "ruff", "check", "src"]
        config = {"profile": {"check_commands": [lint], "unit_commands": [unit, lint]}}
        for role in ("implement", "characterize"):
            with self.subTest(role=role):
                self.assertEqual(_fast_checks(config, role), [lint, unit])
        self.assertEqual(_fast_checks({"profile": {"unit_commands": [unit]}}, "characterize"), [unit])
        self.assertEqual(_fast_checks(config, "review"), [])


if __name__ == "__main__":
    unittest.main()
