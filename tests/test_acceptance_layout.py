"""Per-change acceptance suites live under acceptance/changes/; pre-rename suites stay recognized."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from storyloop.smoke import (LEGACY_PROTECTION_DIR, LEGACY_SUITE_DIR, PROTECTION_DIR, SUITE_DIR,
                             protection_path, suite_relative, suite_root)


class AcceptanceLayoutTests(unittest.TestCase):
    def test_new_suites_go_to_acceptance_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(suite_relative(root, "FEAT-1"), f"{SUITE_DIR}/FEAT-1")
            self.assertEqual(suite_root(root, "FEAT-1"), root.resolve() / SUITE_DIR / "FEAT-1")
            self.assertEqual(protection_path(root, "FEAT-1"), root.resolve() / PROTECTION_DIR / "FEAT-1.json")

    def test_pre_rename_suites_keep_their_legacy_location_and_protection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / LEGACY_SUITE_DIR / "FEAT-0").mkdir(parents=True)
            (root / LEGACY_PROTECTION_DIR).mkdir(parents=True)
            (root / LEGACY_PROTECTION_DIR / "FEAT-0.json").write_text("{}", encoding="utf-8")
            self.assertEqual(suite_relative(root, "FEAT-0"), f"{LEGACY_SUITE_DIR}/FEAT-0")
            self.assertEqual(protection_path(root, "FEAT-0"), root.resolve() / LEGACY_PROTECTION_DIR / "FEAT-0.json")
            # A different change in the same repository still uses the new layout.
            self.assertEqual(suite_relative(root, "FEAT-1"), f"{SUITE_DIR}/FEAT-1")


if __name__ == "__main__":
    unittest.main()
