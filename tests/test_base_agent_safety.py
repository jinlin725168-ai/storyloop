"""Agent base runs cannot judge changes to their own engine."""

import unittest
import os
from pathlib import Path
import tempfile
from unittest import mock

from storyloop import base_agent_execution, base_execution, base_run_open, base_upgrade
from storyloop.approvals import approve_change
from storyloop.models import StoryLoopError
from storyloop.project_loop import _new_run_state, run_change
from storyloop.run_services import LoopServices
from storyloop.run_state import run_path
from storyloop.storage import load_json
from storyloop.base_design import approve_base_design
from tests import test_base_agent_flow as flow
from tests import test_base_lifecycle as lifecycle


class BaseAgentSafetyTests(unittest.TestCase):
    setUp = lifecycle.BaseG1Tests.setUp
    _services = flow.BaseAgentFlowTests._services

    def _approved(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, services=self._services())
        state = load_json(run_path(self.root, change_id))
        approve_base_design(self.root, change_id, state["base_design"]["plan_id"])
        run_change(self.root, change_id, services=self._services())

    def _implement(self, path, content="changed\n"):
        self._approved()
        services = self._services()

        def write(project, _payload):
            target = project / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return {"summary": "updated"}

        configured = LoopServices(services.generate_smoke, write, services.review,
                                  design=services.design)
        return run_change(self.root, self.change["change_id"], services=configured)

    def test_agent_rejects_unapproved_path(self):
        result = self._implement("extra.txt")
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")

    def test_agent_rejects_criterion_path(self):
        result = self._implement("acceptance/storyloop-settings.json")
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")

    def test_agent_rejects_report_path(self):
        result = self._implement(f"reports/storyloop/{self.change['change_id']}/report.json")
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")

    def test_agent_rejects_control_plane_file(self):
        result = self._implement(".storyloop/config.json", "{}\n")
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")
        resumed = run_change(self.root, self.change["change_id"], services=self._services())
        self.assertEqual(resumed["status"], "WORKSPACE_TAINTED")
        self.assertIn("restore", resumed["next"].lower())

    def test_agent_rejects_ci_path(self):
        result = self._implement(".github/workflows/ci.yml")
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")

    def test_agent_rejects_symlink_at_approved_path(self):
        probe = self.root / "symlink-probe"
        try:
            os.symlink("README.md", probe)
        except OSError:
            self.skipTest("Creating symlinks needs platform permission")
        probe.unlink()
        self._approved()
        services = self._services()

        def link(project, _payload):
            (project / "pom.xml").unlink()
            os.symlink("README.md", project / "pom.xml")
            return {"summary": "linked"}

        configured = LoopServices(services.generate_smoke, link, services.review,
                                  design=services.design)
        result = run_change(self.root, self.change["change_id"], services=configured)
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")

    def test_agent_cannot_stage_a_rename(self):
        from storyloop.baseline import _git
        self._approved()
        services = self._services()

        def rename(project, _payload):
            _git(project, "mv", "pom.xml", "renamed.xml")
            return {"summary": "renamed"}

        configured = LoopServices(services.generate_smoke, rename, services.review,
                                  design=services.design)
        result = run_change(self.root, self.change["change_id"], services=configured)
        self.assertEqual(result["status"], "WORKSPACE_TAINTED")

    def test_scratch_configuration_cannot_hide_base_evidence_or_control_writes(self):
        from storyloop.project_loop_guards import _is_scratch
        self.assertFalse(_is_scratch(".storyloop/evidence/BASE-1/base-candidate-green.json",
                                     [".storyloop", "evidence"]))
        self.assertFalse(_is_scratch(".storyloop/state/base-reviews/BASE-1.json",
                                     [".storyloop", "base-reviews"]))

    def test_knowledge_scope_approved_lesson_is_exactly_writable(self):
        from storyloop.project_boundaries import role_path_boundary
        payload = {"change": {"product": {"nature": "base", "base": {
            "scope": "knowledge", "paths": ["openspec/lessons/update.md"]}}}}
        boundary = role_path_boundary(self.root, "implement", payload)
        self.assertIn("openspec/lessons/update.md", boundary["writable_paths"])
        self.assertNotIn("openspec/lessons/**", boundary["forbidden_paths"])

    def test_agent_self_hosting_is_rejected_before_branch_creation(self):
        proposed = base_upgrade.propose_base_change(
            self.root, ["storyloop/run_guards.py"], "调整引擎守卫")
        change = approve_change(self.root, proposed["change_id"])
        with mock.patch.object(base_run_open, "_self_hosted_agent", return_value=True):
            with self.assertRaisesRegex(StoryLoopError, "self.host|T6|自举"):
                base_execution._open_state(self.root, change, "agent", _new_run_state)

    def test_self_hosting_check_resolves_project_alias(self):
        self._track_engine_source()
        alias = self.root / ".." / self.root.name
        with mock.patch.object(base_run_open, "__file__", str(self.root / "storyloop/base_run_open.py")):
            self.assertTrue(base_run_open._self_hosted_agent(
                alias, {"storyloop/run_guards.py"}))

    def test_self_hosted_engine_rejects_agent_even_for_document_path(self):
        self._track_engine_source()
        with mock.patch.object(base_run_open, "__file__", str(self.root / "storyloop/base_run_open.py")):
            self.assertTrue(base_run_open._self_hosted_agent(self.root, {"docs/x.md"}))

    def _track_engine_source(self):
        from storyloop.baseline import _git
        path = self.root / "storyloop/base_run_open.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# tracked engine\n", encoding="utf-8")
        _git(self.root, "add", "--", "storyloop/base_run_open.py")

    def test_nested_engine_rejects_agent_even_for_document_path(self):
        engine_file = self.root / "tools/storyloop/storyloop/base_run_open.py"
        engine_file.parent.mkdir(parents=True)
        engine_file.write_text("# tracked engine\n", encoding="utf-8")
        from storyloop.baseline import _git
        _git(self.root, "add", "--", "tools/storyloop/storyloop/base_run_open.py")
        with mock.patch.object(base_run_open, "__file__", str(engine_file)):
            self.assertTrue(base_run_open._self_hosted_agent(self.root, {"docs/x.md"}))

    def test_tracked_sibling_engine_source_rejects_agent(self):
        from storyloop.baseline import _git
        engine_root = self.root / "tools/storyloop/storyloop"
        engine_root.mkdir(parents=True)
        (engine_root / "run_recovery.py").write_text("# tracked sibling\n", encoding="utf-8")
        _git(self.root, "add", "--", "tools/storyloop/storyloop/run_recovery.py")
        with mock.patch.object(base_run_open, "__file__", str(engine_root / "base_run_open.py")):
            self.assertTrue(base_run_open._self_hosted_agent(self.root, {"docs/x.md"}))

    def test_engine_installed_in_project_venv_allows_agent(self):
        engine_file = self.root / ".venv/lib/site-packages/storyloop/base_run_open.py"
        engine_file.parent.mkdir(parents=True)
        engine_file.write_text("# installed engine\n", encoding="utf-8")
        with mock.patch.object(base_run_open, "__file__", str(engine_file)):
            self.assertFalse(base_run_open._self_hosted_agent(self.root, {"docs/x.md"}))

    def test_tracked_venv_install_is_still_not_self_hosting(self):
        from storyloop.baseline import _git
        engine_file = self.root / ".venv/lib/site-packages/storyloop/base_run_open.py"
        engine_file.parent.mkdir(parents=True)
        engine_file.write_text("# installed engine\n", encoding="utf-8")
        _git(self.root, "add", "-f", "--", ".venv/lib/site-packages/storyloop/base_run_open.py")
        with mock.patch.object(base_run_open, "__file__", str(engine_file)):
            self.assertFalse(base_run_open._self_hosted_agent(self.root, {"docs/x.md"}))

    def test_agent_engine_source_edit_warns_without_blocking_candidate(self):
        self._approved()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        engine = Path(folder.name)
        source = engine / "guard.py"
        source.write_text("trusted\n", encoding="utf-8")
        before = lifecycle.git(self.root, "rev-parse", "HEAD")
        services = self._services()

        def write(project, _payload):
            (project / "pom.xml").write_text("<project>updated</project>\n", encoding="utf-8")
            source.write_text("modified\n", encoding="utf-8")
            return {"summary": "updated"}

        configured = LoopServices(services.generate_smoke, write, services.review,
                                  design=services.design)
        with mock.patch("storyloop.engine_source_safety.engine_source_digest",
                        side_effect=lambda _path: source.read_text(encoding="utf-8")):
            result = run_change(self.root, self.change["change_id"], services=configured)
            candidate_head = lifecycle.git(self.root, "rev-parse", "HEAD")
            resumed = run_change(self.root, self.change["change_id"], services=configured)
        self.assertEqual(result["status"], "BASE_REVIEW_REQUIRED", result)
        self.assertIn("reinstall", str(result).lower())
        self.assertNotEqual(candidate_head, before)
        self.assertNotEqual(resumed["status"], "WORKSPACE_TAINTED")

    def test_design_agent_engine_edit_adds_warning(self):
        source = self.root.parent / "design-engine-source.txt"
        source.write_text("trusted\n", encoding="utf-8")
        self.addCleanup(source.unlink)
        services = self._services()

        def design(project, payload):
            source.write_text("changed\n", encoding="utf-8")
            return services.design(project, payload)

        configured = LoopServices(services.generate_smoke, services.implement, services.review,
                                  design=design)
        with mock.patch("storyloop.engine_source_safety.engine_source_digest",
                        side_effect=lambda _path: source.read_text(encoding="utf-8")):
            result = run_change(self.root, self.change["change_id"], services=configured)
        self.assertEqual(result["status"], "DESIGN_APPROVAL_REQUIRED")
        self.assertIn("reinstall", result["warning"])

    def test_unreadable_engine_digest_warns_without_blocking_design(self):
        with mock.patch("storyloop.base_design.engine_source_digest",
                        side_effect=StoryLoopError("source unreadable")):
            result = run_change(self.root, self.change["change_id"], services=self._services())
        self.assertEqual(result["status"], "DESIGN_APPROVAL_REQUIRED")
        self.assertIn("reinstall", result["warning"])

    def test_review_agent_engine_edit_adds_warning(self):
        self.assertEqual(self._implement("pom.xml", "<project>updated</project>\n")["status"],
                         "BASE_REVIEW_REQUIRED")
        source = self.root.parent / "review-engine-source.txt"
        source.write_text("trusted\n", encoding="utf-8")
        self.addCleanup(source.unlink)
        services = self._services()

        def review(_project, _payload):
            source.write_text("changed\n", encoding="utf-8")
            return {"verdict": "pass", "summary": "checked", "findings": [], "questions": []}

        configured = LoopServices(services.generate_smoke, services.implement, review,
                                  design=services.design)
        context = {"implement_vendor": "openai", "review_vendor": "anthropic",
                   "review_adapter": "reviewer", "available_vendors": {"openai", "anthropic"}}
        with mock.patch("storyloop.base_review.vendor_context", return_value=context):
            with mock.patch("storyloop.engine_source_safety.engine_source_digest",
                            side_effect=lambda _path: source.read_text(encoding="utf-8")):
                result = run_change(self.root, self.change["change_id"], services=configured)
        self.assertEqual(result["status"], "MANUAL_ACCEPTANCE_REQUIRED")
        self.assertIn("reinstall", result["warning"])

    def test_engine_source_digest_detects_changed_source(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        source = root / "guard.py"
        source.write_text("trusted\n", encoding="utf-8")
        digest = getattr(base_agent_execution, "engine_source_digest", lambda _root: "")
        before = digest(root)
        source.write_text("modified\n", encoding="utf-8")
        self.assertNotEqual(digest(root), before)

    def test_engine_git_retains_ambient_global_preferences(self):
        from storyloop.git_command import git as engine_git
        home = self.root / "git-home"
        home.mkdir()
        (home / ".gitconfig").write_text(
            "[user]\n\tname = Global User\n\temail = global@example.invalid\n"
            "[core]\n\tautocrlf = true\n\texcludesFile = ~/.gitignore-global\n",
            encoding="utf-8")
        with mock.patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home),
                                      "XDG_CONFIG_HOME": str(home / ".config")}):
            settings = engine_git(self.root, "config", "--show-origin", "--list")
        self.assertIn("core.excludesfile=~/.gitignore-global", settings)
        self.assertIn("core.autocrlf=true", settings)

    def test_agent_control_binding_excludes_git_config(self):
        before = base_agent_execution.agent_control_binding(self.root, self.change["change_id"])
        config = self.root / ".git/config"
        config.write_bytes(config.read_bytes() + b"\n[branch \"other\"]\n\tremote = origin\n")
        after = base_agent_execution.agent_control_binding(self.root, self.change["change_id"])
        self.assertEqual(after, before)
        self.assertNotIn(".git/config", after)

    def test_approved_knowledge_principles_path_can_be_implemented(self):
        proposal = base_upgrade.propose_base_change(
            self.root, ["openspec/principles.json"], "更新项目原则", scope="knowledge")
        change = approve_change(self.root, proposal["change_id"])
        services = self._services()
        self.assertEqual(run_change(self.root, change["change_id"],
                                    services=services)["status"], "DESIGN_APPROVAL_REQUIRED")
        state = load_json(run_path(self.root, change["change_id"]))
        approve_base_design(self.root, change["change_id"], state["base_design"]["plan_id"])
        run_change(self.root, change["change_id"], services=services)

        def write(project, _payload):
            (project / "openspec/principles.json").write_text("{}\n", encoding="utf-8")
            return {"summary": "updated"}

        configured = LoopServices(services.generate_smoke, write, services.review,
                                  design=services.design)
        self.assertEqual(run_change(self.root, change["change_id"],
                                    services=configured)["status"], "BASE_REVIEW_REQUIRED")


if __name__ == "__main__":
    unittest.main()
