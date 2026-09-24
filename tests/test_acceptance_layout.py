"""Per-change acceptance suites live under acceptance/changes/; pre-rename suites stay recognized."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from storyloop.smoke import (LEGACY_PROTECTION_DIR, LEGACY_SUITE_DIR, PROTECTION_DIR, SUITE_DIR,
                             _sha256, protection_path, suite_relative, suite_root)
from storyloop.system_smoke import _digests as system_digests


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


class LineEndingTests(unittest.TestCase):
    def test_crlf_checkouts_do_not_count_as_modifications_but_content_changes_do(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            lf = root / "lf.py"
            crlf = root / "crlf.py"
            lf.write_bytes(b"import os\nprint(1)\n")
            crlf.write_bytes(b"import os\r\nprint(1)\r\n")
            self.assertEqual(_sha256(lf), _sha256(crlf))
            changed = root / "changed.py"
            changed.write_bytes(b"import os\nprint(2)\n")
            self.assertNotEqual(_sha256(lf), _sha256(changed))
            (root / "acceptance/smoke").mkdir(parents=True)
            (root / "acceptance/smoke/manifest.json").write_bytes(b'{"version": 1}\n')
            before = system_digests(root)
            (root / "acceptance/smoke/manifest.json").write_bytes(b'{"version": 1}\r\n')
            self.assertEqual(system_digests(root), before)


if __name__ == "__main__":
    unittest.main()
