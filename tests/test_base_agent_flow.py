"""The selected base implementer is frozen in a real run."""

from pathlib import Path
import unittest
from unittest import mock

from storyloop import base_execution, base_upgrade
from storyloop.approvals import approve_change
from storyloop.baseline import load_project_config
from storyloop.models import StoryLoopError
from storyloop.project_agents import invoke_project_agent
from storyloop.project_loop import _new_run_state, run_change as _run_change
from storyloop.run_services import LoopServices
from storyloop.run_state import run_path
from storyloop.storage import load_json, write_json
from storyloop.change_log import change_log, render_log
from storyloop.cli import dispatch, parser
from tests import test_base_lifecycle as lifecycle


def run_change(*args, **kwargs):
    """The agent channel is preview-only, so these agent tests opt in explicitly."""
    kwargs.setdefault("implementer", "agent")
    return _run_change(*args, **kwargs)


class BaseAgentFlowTests(unittest.TestCase):
    setUp = lifecycle.BaseG1Tests.setUp

    def test_review_context_uses_error_code_instead_of_message_text(self):
        from storyloop.base_review import agent_review_context
        error = StoryLoopError("renamed error text", code="BASE_REVIEW_UNCONFIGURED")
        with mock.patch("storyloop.base_review.vendor_context", side_effect=error):
            self.assertEqual(agent_review_context(self.root, {"agent_adapter": None})[0], "unknown")
        different = StoryLoopError("Agent base review needs configured adapters",
                                   code="AGENT_UNAVAILABLE")
        with mock.patch("storyloop.base_review.vendor_context", side_effect=different):
            with self.assertRaises(StoryLoopError):
                agent_review_context(self.root, {"agent_adapter": None})

    def test_base_execution_module_stays_within_file_size_limit(self):
        self.assertLessEqual(len(Path(base_execution.__file__).read_text(encoding="utf-8").splitlines()),
                             500)

    def _services(self):
        return LoopServices(
            generate_smoke=lambda *_: {}, implement=lambda *_: {}, review=lambda *_: {},
            design=lambda *_: {"summary": "更新构建配置", "steps": ["编辑 pom.xml"],
                               "risks": ["构建可能失败"], "rollback": "恢复原文件", "questions": []},
        )

    def test_foundation_defaults_to_human_without_preview_flag(self):
        state = base_execution._open_state(self.root, self.change, None, _new_run_state)
        self.assertEqual(state["implementer"], "human")

    def test_agent_run_completes_g1_and_waits_for_design_approval(self):
        result = run_change(self.root, self.change["change_id"], services=self._services(),
                            implementer="agent")
        self.assertEqual(result["status"], "DESIGN_APPROVAL_REQUIRED")
        self.assertEqual(load_json(run_path(self.root, self.change["change_id"]))["implementer"], "agent")

    def test_design_stop_commands_and_summary_match_status_and_log(self):
        change_id = self.change["change_id"]
        result = run_change(self.root, change_id, services=self._services())
        plan = load_json(self.root / ".storyloop/state/base-designs" / f"{change_id}.json")
        command = result["next"]
        self.assertIn("storyloop base-design-approve", command)
        self.assertEqual(result["stop"]["options"][0]["command"], command)
        self.assertEqual(result["stop"]["resume"], command)
        status = dispatch(parser().parse_args(["status", str(self.root), "--change", change_id]))
        log = change_log(self.root, change_id)
        self.assertEqual(status["next"], command)
        self.assertEqual(log["next"], command)
        for view in (result["stop"], status["stop"], log["stop"]):
            summary = view["plan_summary"]
            self.assertEqual(summary["goal"], plan["goal"])
            self.assertEqual(summary["summary"], plan["summary"])
            self.assertEqual(summary["paths"], ["pom.xml"])
            self.assertIn("编辑 pom.xml", summary["steps"])
            self.assertIn("构建可能失败", summary["risks"])
            self.assertEqual(summary["rollback"], "恢复原文件")
        rendered = render_log(log)
        for value in (plan["goal"], "pom.xml", "编辑 pom.xml", "构建可能失败", "恢复原文件"):
            self.assertIn(value, rendered)
        self.assertIn("更新构建配置", plan["goal"])
        self.assertNotIn("\\u", (self.root / ".storyloop/state/base-designs" /
                                  f"{change_id}.json").read_text(encoding="utf-8"))

    def test_approved_design_status_and_log_point_to_run(self):
        from storyloop.base_design import approve_base_design
        change_id = self.change["change_id"]
        run_change(self.root, change_id, services=self._services())
        state = load_json(run_path(self.root, change_id))
        approve_base_design(self.root, change_id, state["base_design"]["plan_id"])
        status = dispatch(parser().parse_args(["status", str(self.root), "--change", change_id]))
        log = change_log(self.root, change_id)
        for view in (status, log):
            self.assertIn("storyloop run", view["next"])
            self.assertNotIn("base-design-approve", view["next"])
            self.assertIn("storyloop run", view["stop"]["resume"])

    def test_ci_defaults_to_human_and_rejects_explicit_agent(self):
        proposed = base_upgrade.propose_base_change(
            self.root, [".github/workflows/ci.yml"], "调整 CI", allow_ci=True)
        change = approve_change(self.root, proposed["change_id"])
        with self.assertRaisesRegex(StoryLoopError, "CI|ci"):
            base_execution._open_state(self.root, change, "agent", _new_run_state)
        state = base_execution._open_state(self.root, change, None, _new_run_state)
        self.assertEqual(state["implementer"], "human")

    def test_upstream_defaults_to_human(self):
        proposed = base_upgrade.propose_base_change(
            self.root, ["modules/updated.py"], "同步上游", allow_mutable=True)
        change = approve_change(self.root, proposed["change_id"])
        state = base_execution._open_state(self.root, change, None, _new_run_state)
        self.assertEqual(state["implementer"], "human")

    def test_saved_e1_run_without_implementer_remains_readable(self):
        base_execution._open_state(self.root, self.change, "human", _new_run_state)
        path = run_path(self.root, self.change["change_id"])
        saved = load_json(path)
        saved.pop("implementer")
        write_json(path, saved)
        before = path.read_bytes()
        state = base_execution._open_state(self.root, self.change, None, _new_run_state)
        self.assertNotIn("implementer", state)
        self.assertEqual(path.read_bytes(), before)

    def test_saved_implementer_cannot_be_switched(self):
        base_execution._open_state(self.root, self.change, "human", _new_run_state)
        with self.assertRaisesRegex(StoryLoopError, "implementer|human"):
            base_execution._open_state(self.root, self.change, "agent", _new_run_state)

    def test_base_transport_request_uses_heavy_routing_and_strong_model(self):
        config = load_project_config(self.root)
        config.update({"adapters": {"one": {"kind": "manual"}},
                       "roles": {"default_adapter": "one"}})
        write_json(self.root / ".storyloop/config.json", config)
        with self.assertRaisesRegex(StoryLoopError, "Manual input"):
            invoke_project_agent(self.root, "implement", {"change": self.change})
        request = load_json(self.root / ".storyloop/requests/project-implement.json")
        self.assertEqual(request["role_profile"]["routing_tier"], "heavy")
        self.assertEqual(request["role_profile"]["tier"], "strong")

    def test_agent_plan_requires_named_approval_before_implementation(self):
        change_id = self.change["change_id"]
        result = run_change(self.root, change_id, services=self._services())
        self.assertEqual(result["status"], "DESIGN_APPROVAL_REQUIRED")
        state = load_json(run_path(self.root, change_id))
        self.assertEqual(state["base_design"]["paths"], ["pom.xml"])
        self.assertEqual(run_change(self.root, change_id, services=self._services())["status"],
                         "DESIGN_APPROVAL_REQUIRED")
        from storyloop.base_design import approve_base_design
        approved = approve_base_design(self.root, change_id, state["base_design"]["plan_id"])
        self.assertEqual(approved["actor"], "StoryLoop Test")
        self.assertEqual(run_change(self.root, change_id, services=self._services())["status"],
                         "IMPLEMENTING")

    def test_human_run_does_not_call_design_agent(self):
        services = self._services()
        services = LoopServices(services.generate_smoke, services.implement, services.review,
                                design=lambda *_: self.fail("human path called design"))
        result = run_change(self.root, self.change["change_id"], services=services,
                            implementer="human")
        self.assertEqual(result["status"], "HUMAN_IMPLEMENTATION_REQUIRED")

    def test_modified_approved_plan_preserves_unknown_human_edit(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, services=self._services())
        from storyloop.base_design import approve_base_design
        state = load_json(run_path(self.root, change_id))
        approve_base_design(self.root, change_id, state["base_design"]["plan_id"])
        plan_path = self.root / ".storyloop/state/base-designs" / f"{change_id}.json"
        plan = load_json(plan_path)
        plan["steps"] = ["偷换步骤"]
        write_json(plan_path, plan)
        self.assertEqual(run_change(self.root, change_id, services=self._services())["status"],
                         "WORKSPACE_TAINTED")
        self.assertTrue(plan_path.exists())
        self.assertEqual(load_json(plan_path)["steps"], ["偷换步骤"])

    def test_design_binding_checks_live_engine_but_uses_frozen_source(self):
        from storyloop import base_design
        from storyloop.base_design import invalid_reason
        change_id = self.change["change_id"]
        run_change(self.root, change_id, services=self._services())
        state = load_json(run_path(self.root, change_id))
        self.assertIsNone(invalid_reason(self.root, self.change, state))
        with mock.patch("storyloop.base_design.__version__", "new-engine", create=True):
            self.assertIsNotNone(invalid_reason(self.root, self.change, state))
        with mock.patch.object(base_design, "_git", return_value="different-source\n"):
            self.assertIsNone(invalid_reason(self.root, self.change, state))

    def test_design_binding_checks_live_contract_product(self):
        from storyloop.base_design import invalid_reason
        change_id = self.change["change_id"]
        run_change(self.root, change_id, services=self._services())
        state = load_json(run_path(self.root, change_id))
        changed = {**self.change, "product": {**self.change["product"], "goal": "different"}}
        self.assertIsNotNone(invalid_reason(self.root, changed, state))

    def test_design_approval_rejects_code_written_before_human_decision(self):
        change_id = self.change["change_id"]
        run_change(self.root, change_id, services=self._services())
        from storyloop.base_design import approve_base_design
        state = load_json(run_path(self.root, change_id))
        (self.root / "pom.xml").write_text("<project><version>2</version></project>\n",
                                            encoding="utf-8")
        with self.assertRaisesRegex(StoryLoopError, "clean|candidate|before approval"):
            approve_base_design(self.root, change_id, state["base_design"]["plan_id"])

    def test_approved_agent_writes_candidate_and_reaches_review(self):
        from storyloop.base_design import approve_base_design
        change_id = self.change["change_id"]
        run_change(self.root, change_id, services=self._services())
        state = load_json(run_path(self.root, change_id))
        approve_base_design(self.root, change_id, state["base_design"]["plan_id"])
        self.assertEqual(run_change(self.root, change_id, services=self._services())["status"],
                         "IMPLEMENTING")

        def implement(project, _payload):
            (project / "pom.xml").write_text("<project><version>2</version></project>\n",
                                               encoding="utf-8")
            return {"summary": "updated"}

        services = self._services()
        services = LoopServices(services.generate_smoke, implement, services.review,
                                design=services.design)
        result = run_change(self.root, change_id, services=services)
        self.assertEqual(result["status"], "BASE_REVIEW_REQUIRED")
        saved = load_json(run_path(self.root, change_id))
        self.assertNotEqual(saved["candidate_commit"], saved["contract_commit"])
        self.assertNotIn("agent_call_head", saved)
        self.assertNotIn("agent_worktree", saved)
        self.assertTrue(saved["base_evidence_sha256"])

    def _agent_candidate(self):
        from storyloop.base_design import approve_base_design
        change_id = self.change["change_id"]
        services = self._services()
        run_change(self.root, change_id, services=services)
        state = load_json(run_path(self.root, change_id))
        approve_base_design(self.root, change_id, state["base_design"]["plan_id"])
        run_change(self.root, change_id, services=services)

        def implement(project, _payload):
            (project / "pom.xml").write_text("<project><version>2</version></project>\n",
                                               encoding="utf-8")
            return {"summary": "updated"}

        writer = LoopServices(services.generate_smoke, implement, services.review,
                              design=services.design)
        result = run_change(self.root, change_id, services=writer)
        self.assertEqual(result["status"], "BASE_REVIEW_REQUIRED")
        return result

    def test_distinct_vendor_review_stop_points_to_agent_run(self):
        context = {"implement_vendor": "openai", "review_vendor": "anthropic",
                   "review_adapter": "reviewer", "available_vendors": {"openai", "anthropic"}}
        with mock.patch("storyloop.base_review.vendor_context", return_value=context):
            result = self._agent_candidate()
        self.assertIn("跨厂商代理审查", result["stop"]["blocker"])
        self.assertEqual(result["next"], f"storyloop run <project> --change {self.change['change_id']}")
        self.assertIn("storyloop run", result["stop"]["options"][0]["command"])
        self.assertNotIn("base-review", str(result["stop"]))

    def test_unrelated_manual_adapter_does_not_hide_distinct_review_vendors(self):
        from storyloop.base_review import vendor_context
        state = {"agent_adapter": "writer", "agent_vendor": "openai"}
        config = {"adapters": {"writer": {"kind": "command"},
                               "reviewer": {"kind": "command"},
                               "manual": {"kind": "manual"}},
                  "roles": {"default_adapter": "writer", "review": {"adapter": "reviewer"}}}
        capabilities = {"writer": {"vendor": "openai"},
                        "reviewer": {"vendor": "anthropic"}, "manual": {}}
        with (mock.patch("storyloop.base_review.load_project_config", return_value=config),
              mock.patch("storyloop.base_review.enabled", return_value=True),
              mock.patch("storyloop.base_review.resolve_adapters", return_value=config["adapters"]),
              mock.patch("storyloop.base_review.role_settings", return_value={"adapter": "reviewer"}),
              mock.patch("storyloop.base_review.capability_for",
                         side_effect=lambda _project, name: capabilities[name])):
            context = vendor_context(self.root, state)
        self.assertEqual(context["available_vendors"], {"openai", "anthropic"})

    def test_unrelated_unknown_adapter_does_not_block_distinct_review(self):
        from storyloop.base_review import agent_review_context
        context = {"implement_vendor": "openai", "review_vendor": "anthropic",
                   "review_adapter": "reviewer", "available_vendors": None}
        with mock.patch("storyloop.base_review.vendor_context", return_value=context):
            self.assertEqual(agent_review_context(self.root, {"agent_adapter": "writer"})[0],
                             "distinct")

    def test_other_proven_vendor_disallows_same_vendor_human_substitute(self):
        from storyloop.base_review import single_vendor_only, vendor_context
        from storyloop.adapter_capabilities import require_base_review_vendors
        state = {"agent_adapter": "writer", "agent_vendor": "openai"}
        adapters = {"writer": {"kind": "command"}, "reviewer2": {"kind": "command"},
                    "claude": {"kind": "command"}, "manual": {"kind": "manual"}}
        config = {"adapters": adapters, "roles": {"default_adapter": "writer",
                                                  "review": {"adapter": "reviewer2"}}}
        capabilities = {"writer": {"vendor": "openai"}, "reviewer2": {"vendor": "openai"},
                        "claude": {"vendor": "anthropic"}, "manual": {}}
        with (mock.patch("storyloop.base_review.load_project_config", return_value=config),
              mock.patch("storyloop.base_review.enabled", return_value=True),
              mock.patch("storyloop.base_review.resolve_adapters", return_value=adapters),
              mock.patch("storyloop.base_review.role_settings", return_value={"adapter": "reviewer2"}),
              mock.patch("storyloop.base_review.capability_for",
                         side_effect=lambda _project, name: capabilities[name])):
            context = vendor_context(self.root, state)
            self.assertEqual(context["available_vendors"], {"openai", "anthropic"})
            self.assertFalse(single_vendor_only(self.root, state, context))
            capabilities["claude"] = {}
            self.assertIsNone(vendor_context(self.root, state)["available_vendors"])
            capabilities["claude"] = {"vendor": "anthropic"}
        with (mock.patch("storyloop.adapter_capabilities.ensure_checked",
                         return_value={"adapters": capabilities}),
              mock.patch("storyloop.adapter_capabilities.resolve_adapters", return_value=adapters),
              mock.patch("storyloop.adapter_capabilities.role_settings",
                         side_effect=[{"adapter": "writer"}, {"adapter": "reviewer2"}])):
            with self.assertRaisesRegex(StoryLoopError, "review|different vendor|跨厂商"):
                require_base_review_vendors(self.root, config)

    def test_implement_vendor_mismatch_discards_candidate_instead_of_waiting_for_handshake(self):
        self._agent_candidate()
        with mock.patch("storyloop.base_review.vendor_context",
                        side_effect=StoryLoopError(
                            "Base implement vendor handshake changed after candidate creation")):
            result = run_change(self.root, self.change["change_id"], services=self._services())
        self.assertEqual(result["status"], "CONTRACT_COMMITTED")
        self.assertNotIn("candidate_commit", load_json(run_path(self.root, self.change["change_id"])))

    def test_single_vendor_review_stop_only_offers_human_substitute(self):
        context = {"implement_vendor": "openai", "review_vendor": "openai",
                   "review_adapter": "one", "available_vendors": {"openai"}}
        with mock.patch("storyloop.base_review.vendor_context", return_value=context):
            result = self._agent_candidate()
        self.assertIn("--human-review", result["next"])
        self.assertIn("--human-review", result["stop"]["options"][0]["command"])

    def test_missing_reviewer_vendor_stops_without_human_fallback(self):
        context = {"implement_vendor": "openai", "review_vendor": None,
                   "review_adapter": "reviewer", "available_vendors": None}
        with mock.patch("storyloop.base_review.vendor_context", return_value=context):
            self._agent_candidate()
            result = run_change(self.root, self.change["change_id"], services=self._services())
        self.assertEqual(result["status"], "BASE_REVIEW_REQUIRED")
        self.assertIn("握手", result["stop"]["blocker"])
        self.assertIn("storyloop adapters check <project>", result["next"])
        self.assertIn("作废", result["stop"]["blocker"])
        self.assertNotIn("base-review", str(result["stop"]))

    def test_distinct_vendor_agent_review_advances_to_acceptance(self):
        self._agent_candidate()
        services = self._services()
        reviewer = LoopServices(services.generate_smoke, services.implement,
                                lambda *_: {"verdict": "pass", "summary": "独立审查通过",
                                            "findings": [], "questions": []},
                                design=services.design)
        context = {"implement_vendor": "openai", "review_vendor": "anthropic",
                   "review_adapter": "reviewer", "available_vendors": {"openai", "anthropic"}}
        with mock.patch("storyloop.base_review.vendor_context", return_value=context):
            result = run_change(self.root, self.change["change_id"], services=reviewer)
        self.assertEqual(result["status"], "MANUAL_ACCEPTANCE_REQUIRED", result)
        saved = load_json(run_path(self.root, self.change["change_id"]))
        self.assertEqual(saved["base_review"]["mode"], "agent")

    def test_agent_review_reads_vendor_context_once(self):
        self._agent_candidate()
        services = self._services()
        reviewer = LoopServices(services.generate_smoke, services.implement,
                                lambda *_: {"verdict": "pass", "summary": "已核对",
                                            "findings": [], "questions": []},
                                design=services.design)
        context = {"implement_vendor": "openai", "review_vendor": "anthropic",
                   "review_adapter": "reviewer", "available_vendors": {"openai", "anthropic"}}
        with mock.patch("storyloop.base_review.vendor_context", return_value=context) as observed:
            self.assertEqual(run_change(self.root, self.change["change_id"],
                                        services=reviewer)["status"], "MANUAL_ACCEPTANCE_REQUIRED")
        self.assertEqual(observed.call_count, 1)

    def test_single_vendor_human_review_requires_reason(self):
        self._agent_candidate()
        from storyloop.base_execution import record_base_review
        context = {"implement_vendor": "openai", "review_vendor": "openai",
                   "review_adapter": "one", "available_vendors": {"openai"}}
        with mock.patch("storyloop.base_review.vendor_context", return_value=context):
            with self.assertRaisesRegex(StoryLoopError, "12|reason|理由"):
                record_base_review(self.root, self.change["change_id"], "pass", "checked",
                                   human_review_reason="too short")
            recorded = record_base_review(
                self.root, self.change["change_id"], "pass", "checked",
                human_review_reason="独立核对了全部变更路径和回归证据")
        self.assertEqual(recorded["mode"], "human_substitute")

    def test_human_review_substitute_rejects_verdict_only_reason(self):
        self._agent_candidate()
        from storyloop.base_execution import record_base_review
        context = {"implement_vendor": "openai", "review_vendor": "openai",
                   "review_adapter": "one", "available_vendors": {"openai"}}
        with mock.patch("storyloop.base_review.vendor_context", return_value=context):
            for reason in ("pass pass pass", "审查通过审查通过审查通过"):
                with self.subTest(reason=reason):
                    with self.assertRaisesRegex(StoryLoopError, "reason|理由|结论"):
                        record_base_review(self.root, self.change["change_id"], "pass", "checked",
                                           human_review_reason=reason)


if __name__ == "__main__":
    unittest.main()
