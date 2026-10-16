"""准备阶段污染后复位，仍必须证明 RED 并采样实现前基线。"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storyloop.models import StoryLoopError
from storyloop.project_loop import LoopServices, load_run_state, run_change
from storyloop.run_recovery import reset_run
from storyloop.smoke import RED_EVIDENCE
from storyloop.smoke_runner import run_smoke_isolated
from storyloop.storage import load_json, write_json
from tests.test_failure_routing import approved, implementer
from tests.test_project_loop import smoke_payload


class PrepareResetTests(unittest.TestCase):
    def _recover(self, stage, legacy=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root)
            calls = []
            services = self._services(root, change_id, calls)
            self._taint(root, change_id, services, stage)
            state = load_run_state(root, change_id)
            self.assertEqual(state["status"], "WORKSPACE_TAINTED")
            self.assertEqual(state["attempt"], 0)
            self.assertNotIn("red_evidence_sha256", state)
            self.assertEqual(calls, [])
            (root / "unexpected.txt").unlink()
            if legacy:
                state.pop("interrupted_status", None)
                write_json(root / f".storyloop/state/runs/{change_id}.json", state)
            reset_run(root, change_id, "已清理只读阶段的越界文件")
            result = run_change(root, change_id, services)
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(result["attempts"], 1)
            self.assertEqual(len(calls), 1)
            self.assertNotIn("interrupted_status", load_run_state(root, change_id))

    def _services(self, root, change_id, calls):
        def implement(project, context):
            state = load_run_state(project, change_id)
            evidence = project / f".storyloop/evidence/{change_id}/{RED_EVIDENCE}"
            self.assertTrue(evidence.is_file(), "实现前缺失 RED 证据")
            self.assertEqual(load_json(evidence)["status"], "RED")
            self.assertTrue(state.get("red_evidence_sha256"))
            self.assertEqual(state["oracle"]["baseline"], {"unit": [], "integration": []})
            return implementer(calls)(project, context)

        return LoopServices(
            generate_smoke=lambda p, c: smoke_payload(change_id), implement=implement,
            review=lambda p, c: {"verdict": "pass", "summary": "通过", "findings": [], "questions": []},
        )

    def _taint(self, root, change_id, services, stage):
        def generate(project, context):
            (project / "unexpected.txt").write_text("污染\n", encoding="utf-8")
            return smoke_payload(change_id)

        def red(project, identifier, **kwargs):
            if kwargs.get("expect_red"):
                (project / "unexpected.txt").write_text("污染\n", encoding="utf-8")
                raise StoryLoopError("RED 执行中断")
            return run_smoke_isolated(project, identifier, **kwargs)

        with self.assertRaises(StoryLoopError) as raised:
            if stage == "acceptance":
                run_change(root, change_id, LoopServices(generate, services.implement, services.review))
            else:
                with patch("storyloop.project_preparation.run_smoke_isolated", side_effect=red):
                    run_change(root, change_id, services)
        self.assertEqual(raised.exception.code, "WORKSPACE_TAINTED")

    def test_acceptance_taint_reset_reenters_preparation(self):
        self._recover("acceptance")

    def test_red_taint_reset_cannot_skip_red_or_baseline(self):
        self._recover("red")

    def test_legacy_prepare_taint_without_saved_stage_cannot_skip_red(self):
        self._recover("red", legacy=True)


if __name__ == "__main__":
    unittest.main()
