"""Report evidence reads files under a project root however that root is spelled."""
import os
import tempfile
import unittest
from pathlib import Path

from storyloop.report_evidence import _read_object
from storyloop.storage import write_json


class EvidenceRootSpellingTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'symlinks need privileges on Windows')
    def test_root_reached_through_a_symlink(self):
        # Same shape as a Windows 8.3 short temp path: the spelling differs from the resolved path.
        with tempfile.TemporaryDirectory() as folder:
            real = Path(folder) / 'real'
            write_json(real / '.storyloop/config.json', {'roles': {}})
            alias = Path(folder) / 'alias'
            alias.symlink_to(real, target_is_directory=True)
            self.assertEqual(_read_object(alias, alias / '.storyloop/config.json', 10_000), {'roles': {}})

    def test_root_in_the_system_temp_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_json(root / '.storyloop/config.json', {'roles': {}})
            self.assertEqual(_read_object(root, root / '.storyloop/config.json', 10_000), {'roles': {}})


if __name__ == '__main__':
    unittest.main()
