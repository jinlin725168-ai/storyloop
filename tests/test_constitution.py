"""Single-source dual constitution generation and identity tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storyloop.constitution import (
    generate_constitutions,
    governance_identity,
    verify_constitutions,
)
from storyloop.models import StoryLoopError
from tests.test_spec_gate import proposal
from tests.test_stage_config import loop_config


class ConstitutionTests(unittest.TestCase):
    def test_generates_byte_identical_agent_files_from_one_source(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            source = project / "governance/L1.md"
            source.parent.mkdir()
            source.write_text("# L1 Constitution\n\nNo secrets in logs.\n", encoding="utf-8")
            digest = generate_constitutions(project, "governance/L1.md")
            self.assertEqual((project / "AGENTS.md").read_bytes(),
                             (project / "CLAUDE.md").read_bytes())
            self.assertEqual(verify_constitutions(project, "governance/L1.md"), digest)

    def test_mismatch_and_unsafe_source_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            source = project / "L1.md"
            source.write_text("governed", encoding="utf-8")
            generate_constitutions(project, "L1.md")
            (project / "CLAUDE.md").write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(StoryLoopError, "match"):
                verify_constitutions(project, "L1.md")
            with self.assertRaises(StoryLoopError):
                generate_constitutions(project, "../outside.md")

    def test_governance_identity_binds_specs_and_constitutions(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            spec = project / config["stages"][0]["specs"][0]
            spec.parent.mkdir(parents=True)
            spec.write_text(proposal(), encoding="utf-8")
            source = project / "governance/L1.md"
            source.parent.mkdir()
            source.write_text("# L1\n", encoding="utf-8")
            generate_constitutions(project, "governance/L1.md")
            before = governance_identity(project, config)
            spec.write_text(proposal().replace("Safe feature", "Changed feature"),
                            encoding="utf-8")
            after = governance_identity(project, config)
        self.assertNotEqual(before, after)
        self.assertEqual(set(before), {"proposal_digests", "constitution_digest"})


if __name__ == "__main__":
    unittest.main()
