"""Existing public scratch-path contract that documentation updates must retain."""

from pathlib import Path
import unittest


class ScratchDocumentationCharacterization(unittest.TestCase):
    def test_api_keeps_relative_path_prefix_contract(self):
        api = Path(__file__).resolve().parents[1] / "docs/api.md"
        content = api.read_text(encoding="utf-8")
        scratch_row = next(line for line in content.splitlines()
                           if line.startswith("| `scratch_paths` |"))
        self.assertIn("含 `/` 的按相对路径前缀匹配", scratch_row)


if __name__ == "__main__":
    unittest.main()
