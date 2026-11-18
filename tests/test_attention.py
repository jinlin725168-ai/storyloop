"""Roadmap G (attention budget): routine stops continue on their own, decisions reach a person."""
from pathlib import Path
import unittest
from unittest import mock

from tests.test_intake import assessed_product

DECISION = {"unresolved": []}


class RoleFixableIntakeTests(unittest.TestCase):
    def test_existence_mislabels_go_to_the_role_and_questions_stay_with_the_person(self):
        from storyloop.intake import needs_input_reasons, role_fixable_reasons
        value = assessed_product()
        value["assessment"]["criteria"][0]["existence"] = "partial"
        value["questions"] = [{"id": "Q1", "text": "?", "options": ["a", "b"], "recommended": "a"}]
        reasons = needs_input_reasons(value, DECISION)
        fixable = role_fixable_reasons(value, DECISION)
        self.assertEqual(len(reasons), 2)
        self.assertEqual(len(fixable), 1)
        self.assertIn("existence 为 partial", fixable[0])
        self.assertEqual(role_fixable_reasons(value, {"unresolved": ["risks.guard"]}), fixable)

    def test_auto_repair_sends_role_fixable_intake_reasons_back_once(self):
        from storyloop import product_cli
        value = assessed_product()
        value["assessment"]["criteria"][0]["existence"] = "partial"
        state = {"change_id": "FEAT-1", "product": value,
                 "intake": {"status": "NEEDS_INPUT", "decision": DECISION}}
        calls = []
        with mock.patch.object(product_cli, "draft_blockers", return_value=[]), \
                mock.patch.object(product_cli, "load_change", return_value=state):
            product_cli.auto_repair(Path("."), state, lambda *args: calls.append(args))
        self.assertEqual(len(calls), 1)
        self.assertIn("existence 为 partial", calls[0][2])


class NetworkRetryTests(unittest.TestCase):
    def _invoke(self, errors):
        from storyloop import cross_vendor
        from storyloop.models import StoryLoopError
        attempts = []

        def call(settings, degraded):
            attempts.append(settings["adapter"])
            if len(attempts) <= len(errors):
                raise StoryLoopError(errors[len(attempts) - 1], code="AGENT_UNAVAILABLE")
            return {"ok": True}
        with mock.patch.object(cross_vendor, "role_settings", return_value={"adapter": "command", "tier": "standard"}), \
                mock.patch.object(cross_vendor, "policy", return_value={"enabled": False}), \
                mock.patch("storyloop.adapter_capabilities.ensure_checked"), \
                mock.patch("storyloop.review_context.verification_active", return_value=False):
            try:
                result = cross_vendor.invoke(Path("."), "implement", {}, {"agent_network_retry_seconds": 0},
                                             "FEAT-1", call)
            except StoryLoopError as error:
                result = error
        return attempts, result

    def test_a_network_failure_is_retried_once_on_the_same_adapter(self):
        attempts, result = self._invoke(["Connection reset by peer: network is unreachable"])
        self.assertEqual(attempts, ["command", "command"])
        self.assertEqual(result, {"ok": True})

    def test_auth_failures_and_a_second_network_failure_reach_the_person(self):
        attempts, result = self._invoke(["Not logged in · Please run /login"])
        self.assertEqual((len(attempts), result.code), (1, "AGENT_UNAVAILABLE"))
        attempts, result = self._invoke(["network is unreachable", "network is unreachable"])
        self.assertEqual((len(attempts), result.code), (2, "AGENT_UNAVAILABLE"))


NOW = __import__("datetime").datetime(2026, 11, 18, tzinfo=__import__("datetime").timezone.utc)


def _project(root):
    from storyloop.baseline import bootstrap_project
    from tests.test_baseline_behavior import repository
    repository(root)
    bootstrap_project(root)
    return root


def _draft(root, idea="idea"):
    from tests.routing_support import legacy_create_change as create_change
    from tests.support import product
    return create_change(root, idea, product(), now=lambda: NOW)["change_id"]


class TouchLedgerTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = _project(Path(temporary.name).resolve())

    def test_state_changing_commands_count_by_kind_and_reads_do_not(self):
        from storyloop.touch_ledger import touches
        from storyloop.cli import main
        import contextlib
        import io
        change_id = _draft(self.root)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            main(["status", str(self.root), "--change", change_id])
            main(["log", str(self.root), "--change", change_id])
            main(["run", str(self.root), "--change", change_id, "--no-interactive"])
            main(["accept", str(self.root), "--change", change_id, "--criterion", "AC-1", "--note", "checked"])
        counted = touches(self.root, change_id)
        self.assertEqual(counted["total"], 2)
        self.assertEqual(counted["by_kind"], {"decision": 1, "draft": 0, "resume": 1, "repair": 0})

    def test_a_revision_answering_open_questions_is_a_decision(self):
        import json
        from argparse import Namespace
        from storyloop.touch_ledger import touch_kind
        change_id = _draft(self.root)
        args = Namespace(command="change", project=self.root, change_id=change_id, message="m", supersede=None)
        self.assertEqual(touch_kind(args), "draft")
        path = self.root / ".storyloop/state/changes" / f"{change_id}.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["product"]["questions"] = [{"id": "Q1"}]
        path.write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(touch_kind(args), "decision")

    def test_touches_reach_the_log_and_the_metrics(self):
        from storyloop.touch_ledger import record_touch, render_touches
        from argparse import Namespace
        change_id = _draft(self.root)
        record_touch(Namespace(command="change", project=self.root, change_id=change_id,
                               message="Q1 选推荐项", supersede=None))
        from storyloop.change_log import change_log
        log = change_log(self.root, change_id)
        self.assertEqual(log["touches"]["by_kind"]["draft"], 1)
        self.assertEqual(render_touches(log["touches"]), "人工触达 1 次（草案修订 1）")
        from storyloop.metrics import build_metrics
        summary = build_metrics(self.root, None, None)["metrics"]["L1"]["summary"]
        self.assertEqual(summary["touches"], 1)

    def test_the_report_summary_shows_the_touches(self):
        from storyloop.report_summary import render_summary
        report = {"change_id": "FEAT-1", "status": "PASSED", "implementation_commit": "c" * 40,
                  "requirement": {"goal": "g", "approval": {"contract_digest": "d"}},
                  "attention": {"total": 3, "by_kind": {"decision": 2, "draft": 1, "resume": 0, "repair": 0}}}
        self.assertIn("- 人工触达 3 次（决定 2、草案修订 1）", render_summary(report))
        self.assertNotIn("人工触达", render_summary({**report, "attention": None}))

    def test_uninitialized_projects_are_never_written(self):
        import tempfile
        from argparse import Namespace
        from storyloop.touch_ledger import record_touch
        with tempfile.TemporaryDirectory() as folder:
            record_touch(Namespace(command="run", project=Path(folder), change_id="FEAT-1"))
            self.assertEqual(list(Path(folder).iterdir()), [])


class InboxTests(unittest.TestCase):
    def test_inbox_lists_pending_decisions_across_projects_oldest_first(self):
        import tempfile
        from storyloop.cli import dispatch, parser
        from storyloop.attention import render_inbox
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            one, two = _project(Path(first).resolve()), _project(Path(second).resolve())
            a, b = _draft(one), _draft(two, "other idea")
            result = dispatch(parser().parse_args(["inbox", str(one), str(two)]))
            self.assertEqual(result["status"], "INBOX")
            self.assertEqual({item["change_id"] for item in result["items"]}, {a, b})
            self.assertTrue(all(item["next"] for item in result["items"]))
            text = render_inbox(result, now=NOW)
            self.assertIn("2 个待你决定（2 个项目）", text)
            self.assertIn(one.name, text)

    def test_an_empty_inbox_says_so(self):
        import tempfile
        from storyloop.attention import inbox, render_inbox
        with tempfile.TemporaryDirectory() as folder:
            result = inbox([_project(Path(folder).resolve())], None)
        self.assertEqual(render_inbox(result), "INBOX  没有待你决定的事项")


class NotifyTests(unittest.TestCase):
    def _configured(self, root, sink):
        import json
        import sys
        path = root / ".storyloop/config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["notify_command"] = [sys.executable, "-c",
                                    f"import sys; open({str(sink)!r}, 'a', encoding='utf-8').write(sys.stdin.read() + '\\n')"]
        path.write_text(json.dumps(config), encoding="utf-8")

    def test_a_command_ending_at_a_decision_notifies_once_and_reads_never_do(self):
        import json
        import tempfile
        from argparse import Namespace
        from storyloop.attention import notify
        with tempfile.TemporaryDirectory() as folder:
            root = _project(Path(folder).resolve())
            sink = Path(folder).parent / (Path(folder).name + "-notified.jsonl")
            self.addCleanup(lambda: sink.unlink(missing_ok=True))
            self._configured(root, sink)
            notify(Namespace(command="status", project=root, change_id="FEAT-1"), {"status": "DRAFT"})
            notify(Namespace(command="run", project=root, change_id="FEAT-1"), {"status": "IMPLEMENTING"})
            self.assertFalse(sink.exists())
            notify(Namespace(command="run", project=root, change_id="FEAT-1"),
                   {"status": "ORACLE_DEFECT", "change_id": "FEAT-1", "next": "storyloop acceptance-repair"})
            notify(Namespace(command="approve", project=root, change_id="FEAT-1"), {"status": "PASSED"})
            events = [json.loads(line) for line in sink.read_text(encoding="utf-8").splitlines() if line]
        self.assertEqual([(item["event"], item["status"]) for item in events],
                         [("decision", "ORACLE_DEFECT"), ("passed", "PASSED")])
        self.assertEqual(events[0]["next"], "storyloop acceptance-repair")

    def test_without_a_notify_command_nothing_runs(self):
        import tempfile
        from argparse import Namespace
        from storyloop.attention import notify
        with tempfile.TemporaryDirectory() as folder:
            root = _project(Path(folder).resolve())
            with mock.patch("subprocess.run") as run:
                notify(Namespace(command="run", project=root, change_id="FEAT-1"), {"status": "PASSED"})
            run.assert_not_called()


class ReadinessBindingTests(unittest.TestCase):
    def test_settings_outside_the_test_layers_do_not_void_a_readiness_approval(self):
        # Trial H: adding a `github` section to config.json sent two new drafts back to
        # READINESS_APPROVAL_REQUIRED although nothing about the tests had changed.
        import json
        import tempfile
        from storyloop.readiness import _binding
        with tempfile.TemporaryDirectory() as folder:
            root = _project(Path(folder).resolve())
            before = _binding(root)[0]["test_config_digest"]
            path = root / ".storyloop/config.json"
            config = json.loads(path.read_text(encoding="utf-8"))
            path.write_text(json.dumps({**config, "github": {"repo": "o/r"}, "notify_command": ["x"],
                                        "workers_max": 2}), encoding="utf-8")
            self.assertEqual(_binding(root)[0]["test_config_digest"], before)
            config["check_timeout_seconds"] = 1
            path.write_text(json.dumps(config), encoding="utf-8")
            self.assertNotEqual(_binding(root)[0]["test_config_digest"], before)


if __name__ == "__main__":
    unittest.main()
