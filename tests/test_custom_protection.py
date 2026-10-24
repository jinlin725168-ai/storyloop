"""Customization is human-owned: outside the base fingerprint and agent write scope."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from storyloop.baseline import bootstrap_project, load_baseline, verify_baseline
from storyloop.models import StoryLoopError
from storyloop.project_boundaries import audit_project_changes, role_path_boundary
from storyloop.project_loop_support import control_digest
from storyloop.skill_custom import custom_paths, write_view
from tests.test_baseline_behavior import git, repository


class CustomProtectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        repository(self.root)
        self.team, self.user = custom_paths(self.root, "spec-craft")

    def write(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("version = 1\n", encoding="utf-8")

    def test_agent_write_to_custom_files_is_a_violation(self):
        bootstrap_project(self.root)
        self.write(self.team)
        boundary = role_path_boundary(self.root, "implement", {})
        result = audit_project_changes(self.root, "FEAT-20261024-001")
        self.assertIn("storyloop-custom/**", boundary["forbidden_paths"])
        self.assertEqual(result["status"], "BOUNDARY_VIOLATION")
        self.assertEqual(result["violations"][0]["category"], "CUSTOM_PROTECTED")

    def test_custom_files_are_not_foundation(self):
        self.write(self.team)
        git(self.root, "add", "storyloop-custom/spec-craft.toml")
        git(self.root, "commit", "-q", "-m", "team config")
        bootstrap_project(self.root)
        self.assertNotIn("storyloop-custom/spec-craft.toml", load_baseline(self.root)["foundation_paths"])

    def test_committing_team_customization_does_not_drift_the_base(self):
        bootstrap_project(self.root)
        self.write(self.team)
        git(self.root, "add", "storyloop-custom/spec-craft.toml")
        git(self.root, "commit", "-q", "-m", "team config")
        self.assertEqual(verify_baseline(self.root)["status"], "BASELINE_VERIFIED")

    def test_write_view_excludes_the_user_file_from_git(self):
        self.write(self.user)
        write_view(self.root, "spec-craft")
        exclude = (self.root / ".git/info/exclude").read_text(encoding="utf-8")
        self.assertIn("storyloop-custom/*.user.toml", exclude)
        self.assertEqual(git(self.root, "check-ignore", "storyloop-custom/spec-craft.user.toml").strip(),
                         "storyloop-custom/spec-craft.user.toml")

    def test_view_before_init_keeps_repository_clean_and_initializable(self):
        write_view(self.root, "spec-craft")
        self.assertEqual(git(self.root, "status", "--porcelain=v1", "--untracked-files=all"), "")
        self.assertEqual(bootstrap_project(self.root)["status"], "BASELINE_LOCKED")

    def test_init_excludes_personal_customization_without_view(self):
        bootstrap_project(self.root)
        self.write(self.user)
        self.assertEqual(git(self.root, "check-ignore", "storyloop-custom/spec-craft.user.toml"),
                         "storyloop-custom/spec-craft.user.toml")
        self.assertEqual(verify_baseline(self.root)["status"], "BASELINE_VERIFIED")

    def test_view_does_not_change_initialized_control_digest(self):
        bootstrap_project(self.root)
        before = control_digest(self.root, "FEAT-20261024-001")
        write_view(self.root, "spec-craft")
        self.assertEqual(control_digest(self.root, "FEAT-20261024-001"), before)

    def test_non_utf8_git_exclude_has_a_domain_error(self):
        (self.root / ".git/info/exclude").write_bytes(b"\xff\n")
        with self.assertRaisesRegex(StoryLoopError, "Git exclude"):
            write_view(self.root, "spec-craft")

    def test_tracked_user_file_is_reported(self):
        self.write(self.user)
        write_view(self.root, "spec-craft")
        git(self.root, "add", "-f", "storyloop-custom/spec-craft.user.toml")
        result = write_view(self.root, "spec-craft")
        self.assertTrue(any("已被 Git 跟踪" in warning for warning in result["warnings"]))
