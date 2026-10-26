"""A scripted pair of adapter vendors completes the full base agent channel."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from storyloop import base_execution
from storyloop import base_agent_execution
from storyloop.approvals import approve_change
from storyloop.base_design import approve_base_design
from storyloop.base_upgrade import propose_base_change
from storyloop.baseline import load_baseline
from storyloop.manual_acceptance import record_manual_acceptance
from storyloop.models import StoryLoopError
from storyloop.onboarding_cli import bootstrap_with_project_checks
from storyloop.project_loop import run_change
from storyloop.run_state import run_path
from storyloop.run_recovery import reset_run
from storyloop.storage import load_json
from tests.test_baseline_behavior import git, repository


def _capability(vendor):
    return {"known": True, "vendor": vendor, "tiers": ["strong", "standard"],
            "subagents": False, "path_guard": True, "fast_checks": False, "modes": ["*"]}


class BaseAgentEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name) / "project"
        self.root.mkdir()
        repository(self.root)
        smoke = self.root / "acceptance/smoke"
        smoke.mkdir(parents=True)
        (smoke / "check.py").write_text("print('smoke green')\n", encoding="utf-8")
        (smoke / "manifest.json").write_text(
            '{"version":1,"tests":[{"id":"check","description":"check",'
            '"command":["{python}","acceptance/smoke/check.py"],"timeout_seconds":30}]}',
            encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "smoke")
        adapter = Path(__file__).resolve().parents[1] / "storyloop/testing/mock_adapter.py"
        implement_script = Path(self.folder.name) / "implement.json"
        review_script = Path(self.folder.name) / "review.json"
        implement_script.write_text(json.dumps({"capabilities": _capability("openai"), "calls": [
            {"role": "design", "mode": "base-plan", "result": {
                "summary": "更新构建配置", "steps": ["编辑 pom.xml"],
                "risks": ["构建可能失败"], "rollback": "恢复 pom.xml", "questions": []}},
            {"role": "implement", "mode": "base-implement",
             "creates": [{"path": "pom.xml", "content": "<project><version>2</version></project>\n"}],
             "result": {"summary": "updated", "questions": []}},
        ]}), encoding="utf-8")
        review_script.write_text(json.dumps({"capabilities": _capability(
            getattr(self, "review_vendor", "anthropic")), "calls": [
            {"role": "review", "mode": "base-review", "result": {
                "verdict": "pass", "summary": "独立审查通过", "findings": [],
                "questions": [], "coverage": []}},
        ]}), encoding="utf-8")
        command = lambda script: {"kind": "command", "agent_command": [
            sys.executable, str(adapter), "--script", str(script)]}
        bootstrap_with_project_checks(self.root, {
            "unit_commands": [[sys.executable, "-c", "print('unit green')"]],
            "integration_commands": [], "check_commands": [], "base_build_commands": [],
            "adapters": {"writer": command(implement_script), "reviewer": command(review_script)},
            "roles": {"default_adapter": "writer", "review": {"adapter": "reviewer"}},
        })
        proposal = propose_base_change(self.root, ["pom.xml"], "更新构建配置")
        self.change = approve_change(self.root, proposal["change_id"])

    def test_two_vendor_agent_channel_increments_baseline(self):
        change_id = self.change["change_id"]
        self._accepted_agent_candidate()
        self.assertEqual(run_change(self.root, change_id)["status"], "FINALIZING")
        self.assertEqual(run_change(self.root, change_id)["status"], "PASSED")
        self.assertEqual(load_baseline(self.root)["baseline_version"], "1.0.1")
        report = load_json(self.root / "reports/storyloop" / change_id / "report.json")
        self.assertEqual(report["review_mode"], "agent")
        with self.assertRaisesRegex(StoryLoopError, "Merged base runs cannot reset"):
            reset_run(self.root, change_id, "已合并后不得回准备阶段")

    def _accepted_agent_candidate(self):
        change_id = self.change["change_id"]
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "DESIGN_APPROVAL_REQUIRED")
        state = load_json(run_path(self.root, change_id))
        approve_base_design(self.root, change_id, state["base_design"]["plan_id"])
        self.assertEqual(run_change(self.root, change_id)["status"], "IMPLEMENTING")
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "MANUAL_ACCEPTANCE_REQUIRED")
        record_manual_acceptance(self.root, change_id, ["BASE-ACCEPT"], "已人工核对")

    def test_report_commit_before_verified_state_discards_agent_attempt(self):
        self._accepted_agent_candidate()
        original = base_execution.save_run

        def interrupt(project, state):
            if state["status"] == "VERIFIED":
                raise RuntimeError("crash after report commit")
            return original(project, state)

        with mock.patch.object(base_execution, "save_run", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "crash after report commit"):
                run_change(self.root, self.change["change_id"])
        self.assertEqual(run_change(self.root, self.change["change_id"])["status"],
                         "CONTRACT_COMMITTED")
        self.assertEqual(git(self.root, "rev-parse", "HEAD"),
                         load_json(run_path(self.root, self.change["change_id"]))["contract_commit"])

    def test_merge_before_merged_state_finishes_idempotently(self):
        self._accepted_agent_candidate()
        original = base_execution.save_run

        def interrupt(project, state):
            if state["status"] == "MERGED":
                raise RuntimeError("crash after source merge")
            return original(project, state)

        with mock.patch.object(base_execution, "save_run", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "crash after source merge"):
                run_change(self.root, self.change["change_id"])
        state = load_json(run_path(self.root, self.change["change_id"]))
        self.assertEqual(state["status"], "VERIFIED")
        git(self.root, "switch", state["feature_branch"])
        self.assertEqual(run_change(self.root, self.change["change_id"])["status"],
                         "FINALIZING")
        self.assertEqual(run_change(self.root, self.change["change_id"])["status"], "PASSED")

    def test_unsaved_candidate_already_on_source_cannot_be_discarded(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id)
        state = load_json(run_path(self.root, change_id))
        approve_base_design(self.root, change_id, state["base_design"]["plan_id"])
        run_change(self.root, change_id)
        original = base_agent_execution.save_run

        def interrupt(project, candidate):
            if candidate.get("candidate_commit"):
                raise RuntimeError("crash before candidate state")
            return original(project, candidate)

        with mock.patch.object(base_agent_execution, "save_run", side_effect=interrupt):
            with self.assertRaises(RuntimeError):
                run_change(self.root, change_id)
        saved = load_json(run_path(self.root, change_id))
        candidate = git(self.root, "rev-parse", "HEAD")
        git(self.root, "switch", saved["source_branch"])
        git(self.root, "merge", "--ff-only", saved["feature_branch"])
        git(self.root, "switch", saved["feature_branch"])
        with self.assertRaisesRegex(StoryLoopError, "source|merged|源分支"):
            run_change(self.root, change_id)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), candidate)

    def test_agent_reset_discards_candidate_and_requires_new_design(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id)
        state = load_json(run_path(self.root, change_id))
        approve_base_design(self.root, change_id, state["base_design"]["plan_id"])
        run_change(self.root, change_id)
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")
        self.assertEqual(reset_run(self.root, change_id, "重新开始代理实现")["status"],
                         "CONTRACT_COMMITTED")
        self.assertEqual(git(self.root, "rev-parse", "HEAD"),
                         load_json(run_path(self.root, change_id))["contract_commit"])
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "DESIGN_APPROVAL_REQUIRED")

    def test_staged_report_commit_failure_cleans_index_on_restart(self):
        self._accepted_agent_candidate()
        original = base_execution.commit_paths

        def blocked(project, paths, message, **kwargs):
            if message.startswith("docs("):
                git(project, "add", "--", *paths)
                raise StoryLoopError("signing failed", code="GIT_BLOCKED")
            return original(project, paths, message, **kwargs)

        with mock.patch.object(base_execution, "commit_paths", side_effect=blocked):
            with self.assertRaisesRegex(StoryLoopError, "signing failed"):
                run_change(self.root, self.change["change_id"])
        self.assertEqual(run_change(self.root, self.change["change_id"])["status"],
                         "CONTRACT_COMMITTED")
        self.assertFalse(git(self.root, "diff", "--cached", "--name-only"))

    def test_report_files_before_commit_are_discarded(self):
        self._accepted_agent_candidate()
        original = base_execution.commit_paths

        def interrupted(project, paths, message, **kwargs):
            if message.startswith("docs("):
                raise RuntimeError("crash before report commit")
            return original(project, paths, message, **kwargs)

        with mock.patch.object(base_execution, "commit_paths", side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, "crash before report commit"):
                run_change(self.root, self.change["change_id"])
        report = self.root / "reports/storyloop" / self.change["change_id"] / "report.json"
        self.assertTrue(report.exists())
        self.assertEqual(run_change(self.root, self.change["change_id"])["status"],
                         "CONTRACT_COMMITTED")
        self.assertFalse(report.exists())

    def test_capability_cache_write_before_agent_anchor_restarts(self):
        from storyloop import adapter_capabilities
        change_id = self.change["change_id"]
        run_change(self.root, change_id)
        state = load_json(run_path(self.root, change_id))
        approve_base_design(self.root, change_id, state["base_design"]["plan_id"])
        run_change(self.root, change_id)
        original = adapter_capabilities.ensure_checked

        def interrupt(project):
            original(project)
            raise RuntimeError("crash after adapter cache check")

        with mock.patch.object(adapter_capabilities, "ensure_checked", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "crash after adapter cache check"):
                run_change(self.root, change_id)
        self.assertEqual(run_change(self.root, change_id)["status"], "CONTRACT_COMMITTED")

    def test_modified_handshake_after_candidate_stops_before_review(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id)
        state = load_json(run_path(self.root, change_id))
        approve_base_design(self.root, change_id, state["base_design"]["plan_id"])
        run_change(self.root, change_id)
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")
        path = self.root / ".storyloop/state/adapters.json"
        cached = load_json(path)
        cached["adapters"]["reviewer"]["vendor"] = "openai"
        from storyloop.storage import write_json
        write_json(path, cached)
        self.assertEqual(run_change(self.root, change_id)["status"], "WORKSPACE_TAINTED")

    def test_tampered_handshake_cannot_create_human_substitute_review(self):
        from storyloop.base_execution import record_base_review
        from storyloop.storage import write_json
        change_id = self.change["change_id"]
        run_change(self.root, change_id)
        state = load_json(run_path(self.root, change_id))
        approve_base_design(self.root, change_id, state["base_design"]["plan_id"])
        run_change(self.root, change_id)
        run_change(self.root, change_id)
        path = self.root / ".storyloop/state/adapters.json"
        cached = load_json(path)
        cached["adapters"]["reviewer"]["vendor"] = "openai"
        write_json(path, cached)
        with self.assertRaisesRegex(StoryLoopError, "handshake|configuration|changed"):
            record_base_review(self.root, change_id, "pass", "已核对",
                               human_review_reason="经独立检查所有路径和回归后确认")


class BaseAgentSingleVendorTests(unittest.TestCase):
    setUp = BaseAgentEndToEndTests.setUp
    review_vendor = "openai"

    def test_named_human_substitute_completes_and_counts_separately(self):
        from storyloop.base_execution import record_base_review
        from storyloop.metrics import build_metrics
        change_id = self.change["change_id"]
        run_change(self.root, change_id)
        state = load_json(run_path(self.root, change_id))
        approve_base_design(self.root, change_id, state["base_design"]["plan_id"])
        run_change(self.root, change_id)
        self.assertEqual(run_change(self.root, change_id)["status"], "BASE_REVIEW_REQUIRED")
        paused = run_change(self.root, change_id)
        self.assertIn("--human-review", paused["next"])
        recorded = record_base_review(
            self.root, change_id, "pass", "审查路径和回归证据",
            human_review_reason="项目仅有一个厂商，已由独立人员逐项核对")
        self.assertEqual(recorded["mode"], "human_substitute")
        self.assertEqual(run_change(self.root, change_id)["status"],
                         "MANUAL_ACCEPTANCE_REQUIRED")
        record_manual_acceptance(self.root, change_id, ["BASE-ACCEPT"], "已验收")
        self.assertEqual(run_change(self.root, change_id)["status"], "FINALIZING")
        self.assertEqual(run_change(self.root, change_id)["status"], "PASSED")
        report = load_json(self.root / "reports/storyloop" / change_id / "report.json")
        self.assertEqual(report["review_mode"], "human_substitute")
        self.assertIn("仅有一个厂商", report["human_review_reason"])
        self.assertEqual(build_metrics(self.root, None, None)["metrics"]["base_human_review_substitutions"]["numerator"], 1)


if __name__ == "__main__":
    unittest.main()
