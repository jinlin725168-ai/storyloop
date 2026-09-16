"""Read-only OpenSpec proposal gate tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storyloop.models import StoryLoopError
from storyloop.spec_gate import validate_proposal


def proposal(mapping: str | None = None) -> str:
    rows = mapping or "| AC-1 | CoreTest#works | L1 |\n| AC-2 | EdgeTest#fails | L2 |"
    return f"""# Change: Safe feature
## Why
Needed for users.
## Spec Delta
Add bounded behavior.
## Acceptance Criteria
- AC-1: When input is valid, the system shall return a result
- AC-2: When input is invalid, the system shall reject it
## Scope
- api
## Test Mapping
| AC | Test | Level |
|----|------|-------|
{rows}
## Out of Scope
- deployment
"""


class SpecGateTests(unittest.TestCase):
    def test_validates_ears_ids_and_complete_test_mapping(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            path = project / "openspec/changes/safe/proposal.md"
            path.parent.mkdir(parents=True)
            path.write_text(proposal(), encoding="utf-8")
            result = validate_proposal(project, "openspec/changes/safe/proposal.md")
        self.assertEqual(result["acceptance_ids"], ["AC-1", "AC-2"])
        self.assertEqual([row["ac"] for row in result["test_mapping"]],
                         ["AC-1", "AC-2"])
        self.assertEqual(len(result["digest"]), 64)

    def test_rejects_duplicate_missing_and_unknown_mappings(self):
        cases = [
            proposal("| AC-1 | A#one | L1 |"),
            proposal("| AC-1 | A#one | L1 |\n| AC-1 | A#two | L2 |\n"
                     "| AC-2 | B#two | L2 |"),
            proposal("| AC-1 | A#one | L1 |\n| AC-3 | B#three | L2 |"),
        ]
        for content in cases:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as folder:
                project = Path(folder)
                path = project / "proposal.md"
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(StoryLoopError):
                    validate_proposal(project, "proposal.md")

    def test_rejects_non_ears_acceptance_and_duplicate_ids(self):
        invalid = [
            proposal().replace("When input is valid, the system shall", "Given input works"),
            proposal().replace("- AC-2:", "- AC-1:"),
        ]
        for content in invalid:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as folder:
                project = Path(folder)
                (project / "proposal.md").write_text(content, encoding="utf-8")
                with self.assertRaises(StoryLoopError):
                    validate_proposal(project, "proposal.md")

    def test_rejects_unsafe_paths_and_oversized_markdown(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            outside = project.parent / "outside.md"
            outside.write_text(proposal(), encoding="utf-8")
            with self.assertRaises(StoryLoopError):
                validate_proposal(project, "../outside.md")
            huge = project / "huge.md"
            huge.write_text(proposal() + "x" * (1024 * 1024), encoding="utf-8")
            with self.assertRaisesRegex(StoryLoopError, "size"):
                validate_proposal(project, "huge.md")


if __name__ == "__main__":
    unittest.main()
