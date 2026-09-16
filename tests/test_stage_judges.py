"""Tests for one fail-closed v2 Judge execution boundary."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storyloop.models import StoryLoopError
from storyloop.stage_judges import run_stage_judge
from tests.test_stage_config import loop_config


def response_for(request: dict, status: str = "pass", findings=None) -> dict:
    return {
        "protocol": "loop.judge.v2",
        "request_id": request["request_id"],
        "stage": request["stage"],
        "judge": request["judge"],
        "phase": request["phase"],
        "status": status,
        "summary": "judge summary",
        "findings": (["behavior differs"] if status == "fail" else [])
        if findings is None else findings,
    }


def process_result(stdout: str = "", returncode: int = 0, **changes) -> dict:
    result = {
        "returncode": returncode,
        "stdout": stdout,
        "stderr": "",
        "timed_out": False,
        "truncated": False,
        "error": "",
        "contained": True,
    }
    result.update(changes)
    return result


class StageJudgeTests(unittest.TestCase):
    def project(self, folder: str) -> Path:
        project = Path(folder) / "project"
        project.mkdir()
        (project / "README.md").write_text("stable", encoding="utf-8")
        return project

    def test_executes_only_the_exact_configured_judge_with_structured_input(self):
        config = loop_config()
        captured = {}

        def run(command, cwd, timeout, **kwargs):
            captured.update(command=command, cwd=cwd, timeout=timeout, **kwargs)
            request = json.loads(kwargs["input_text"])
            return process_result(json.dumps(response_for(request)))

        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.stage_judges.run_process", side_effect=run):
            result = run_stage_judge(
                self.project(folder), config, "foundation", "core", {"变更": "上下文"},
                resolver=lambda name: "C:/python.exe",
                environ={"PATH": "hidden", "API_KEY": "secret"},
            )

        request = json.loads(captured["input_text"])
        self.assertEqual(captured["command"], ["C:/python.exe", "acceptance/check.py", "core"])
        self.assertEqual(request["protocol"], "loop.judge.request.v2")
        self.assertEqual((request["stage"], request["judge"], request["phase"]),
                         ("foundation", "core", "l1"))
        self.assertEqual(captured["environment_allowlist"], ["PATH", "JAVA_HOME"])
        self.assertEqual(result["outcome"], "GREEN")
        self.assertEqual(result["before_digest"], result["after_digest"])
        self.assertNotIn("secret", captured["input_text"])

    def test_unknown_stage_judge_and_non_json_context_do_not_start_process(self):
        config = loop_config()
        cases = (("missing", "red", {}, "Unknown stage"),
                 ("foundation", "missing", {}, "Unknown judge"),
                 ("foundation", "red", [], "JSON object"),
                 ("foundation", "red", {"bad": object()}, "bounded JSON"))
        for stage, judge, context, expected in cases:
            with self.subTest(expected=expected), \
                    patch("storyloop.stage_judges.run_process") as run:
                with self.assertRaisesRegex(StoryLoopError, expected):
                    run_stage_judge(Path.cwd(), config, stage, judge, context,
                                    resolver=lambda name: "C:/python.exe")
                run.assert_not_called()

    def test_red_phase_distinguishes_valid_red_from_existing_green(self):
        config = loop_config()

        def run_status(status, code):
            def run(command, cwd, timeout, **kwargs):
                request = json.loads(kwargs["input_text"])
                return process_result(json.dumps(response_for(request, status)), code)
            return run

        for status, code, outcome in (("fail", 1, "RED"), ("pass", 0, "EXISTING_GREEN"),
                                      ("error", 2, "ERROR"), ("skip", 3, "SKIP")):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as folder, \
                    patch("storyloop.stage_judges.run_process",
                          side_effect=run_status(status, code)):
                result = run_stage_judge(
                    self.project(folder), config, "foundation", "red", {},
                    resolver=lambda name: "C:/python.exe",
                )
                self.assertEqual(result["outcome"], outcome)

    def test_non_red_failure_is_not_reported_as_red(self):
        config = loop_config()

        def run(command, cwd, timeout, **kwargs):
            request = json.loads(kwargs["input_text"])
            return process_result(json.dumps(response_for(request, "fail")), 1)

        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.stage_judges.run_process", side_effect=run):
            result = run_stage_judge(
                self.project(folder), config, "foundation", "core", {},
                resolver=lambda name: "C:/python.exe",
            )

        self.assertEqual(result["outcome"], "FAIL")

    def test_exit_code_and_protocol_status_must_agree(self):
        config = loop_config()

        def run(command, cwd, timeout, **kwargs):
            request = json.loads(kwargs["input_text"])
            return process_result(json.dumps(response_for(request, "pass")), 1)

        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.stage_judges.run_process", side_effect=run):
            with self.assertRaisesRegex(StoryLoopError, "inconsistent"):
                run_stage_judge(
                    self.project(folder), config, "foundation", "red", {},
                    resolver=lambda name: "C:/python.exe",
                )

    def test_request_id_binds_workspace_snapshot_and_resolved_command(self):
        config = loop_config()
        request_ids = []

        def run(command, cwd, timeout, **kwargs):
            request = json.loads(kwargs["input_text"])
            request_ids.append(request["request_id"])
            return process_result(json.dumps(response_for(request)))

        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.stage_judges.run_process", side_effect=run):
            project = self.project(folder)
            run_stage_judge(project, config, "foundation", "core", {},
                            resolver=lambda name: "C:/python.exe")
            (project / "README.md").write_text("new code", encoding="utf-8")
            run_stage_judge(project, config, "foundation", "core", {},
                            resolver=lambda name: "C:/python.exe")
            config["stages"][0]["judges"][1]["command"].append("--changed")
            run_stage_judge(project, config, "foundation", "core", {},
                            resolver=lambda name: "C:/python.exe")

        self.assertEqual(len(set(request_ids)), 3)

    def test_containment_is_checked_before_snapshot_or_output(self):
        config = loop_config()
        unsafe = process_result("not-json", contained=False)

        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.stage_judges.run_process", return_value=unsafe), \
                patch("storyloop.stage_judges.capture_snapshot") as snapshot:
            snapshot.return_value.root = self.project(folder)
            with self.assertRaisesRegex(
                StoryLoopError, "securely contained"
            ) as caught:
                run_stage_judge(
                    snapshot.return_value.root, config, "foundation", "red", {},
                    resolver=lambda name: "C:/python.exe",
                )
            self.assertEqual(caught.exception.code, "UNSAFE_CONTAINMENT")
            snapshot.assert_called_once()

    def test_process_failures_do_not_echo_untrusted_output(self):
        config = loop_config()
        cases = ((process_result("SECRET", timed_out=True), "time budget"),
                 (process_result("SECRET", truncated=True), "output limit"),
                 (process_result("SECRET", error="SECRET"), "process failed"))
        for process, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as folder, \
                    patch("storyloop.stage_judges.run_process", return_value=process):
                with self.assertRaisesRegex(StoryLoopError, expected) as caught:
                    run_stage_judge(
                        self.project(folder), config, "foundation", "red", {},
                        resolver=lambda name: "C:/python.exe",
                    )
                self.assertNotIn("SECRET", str(caught.exception))

    def test_strict_response_rejects_unknown_duplicate_and_mismatched_fields(self):
        config = loop_config()

        def unknown(command, cwd, timeout, **kwargs):
            request = json.loads(kwargs["input_text"])
            return process_result(json.dumps({**response_for(request), "passed": True}))

        def duplicate(command, cwd, timeout, **kwargs):
            request = json.loads(kwargs["input_text"])
            output = json.dumps(response_for(request)).replace(
                '"status": "pass"', '"status": "pass", "status": "fail"')
            return process_result(output)

        def stale(command, cwd, timeout, **kwargs):
            request = json.loads(kwargs["input_text"])
            return process_result(json.dumps({**response_for(request), "request_id": "0" * 64}))

        for run, expected in ((unknown, "Unknown contract fields"),
                              (duplicate, "duplicate"), (stale, "current request")):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as folder, \
                    patch("storyloop.stage_judges.run_process", side_effect=run):
                with self.assertRaisesRegex(StoryLoopError, expected):
                    run_stage_judge(
                        self.project(folder), config, "foundation", "red", {},
                        resolver=lambda name: "C:/python.exe",
                    )

    def test_fail_requires_findings_and_pass_forbids_them(self):
        config = loop_config()

        def run_result(status, findings, code):
            def run(command, cwd, timeout, **kwargs):
                request = json.loads(kwargs["input_text"])
                return process_result(json.dumps(response_for(request, status, findings)), code)
            return run

        cases = (("fail", [], 1, "fail requires findings"),
                 ("pass", ["contradiction"], 0, "pass cannot contain findings"))
        for status, findings, code, expected in cases:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as folder, \
                    patch("storyloop.stage_judges.run_process",
                          side_effect=run_result(status, findings, code)):
                with self.assertRaisesRegex(StoryLoopError, expected):
                    run_stage_judge(
                        self.project(folder), config, "foundation", "red", {},
                        resolver=lambda name: "C:/python.exe",
                    )

    def test_real_process_can_emit_a_bound_valid_red(self):
        config = loop_config()
        code = (
            "import json,sys; r=json.load(sys.stdin); "
            "print(json.dumps({'protocol':'loop.judge.v2','request_id':r['request_id'],"
            "'stage':r['stage'],'judge':r['judge'],'phase':r['phase'],'status':'fail',"
            "'summary':'real red','findings':['assertion failed']})); sys.exit(1)"
        )
        config["stages"][0]["judges"][0]["command"] = [sys.executable, "-c", code]

        with tempfile.TemporaryDirectory() as folder:
            result = run_stage_judge(
                self.project(folder), config, "foundation", "red", {},
                resolver=lambda name: sys.executable,
            )

        self.assertEqual(result["outcome"], "RED")
        self.assertIs(result["contained"], True)

    def test_judge_process_uses_the_shared_absolute_deadline(self):
        config = loop_config()
        captured = {}

        def run(command, cwd, timeout, **kwargs):
            captured.update(timeout=timeout, **kwargs)
            request = json.loads(kwargs["input_text"])
            return process_result(json.dumps(response_for(request)))

        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.stage_judges.run_process", side_effect=run):
            run_stage_judge(
                self.project(folder), config, "foundation", "core", {},
                resolver=lambda name: "C:/python.exe",
                attempt_deadline_ms=2_500, budget_clock_ms=lambda: 500,
            )

        self.assertEqual(captured["timeout"], 2.0)
        self.assertEqual(captured["absolute_deadline"], 2.5)
    def test_uninspectable_post_judge_snapshot_is_workspace_tainted(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder:
            project = self.project(folder)

            def run(command, cwd, timeout, **kwargs):
                (project / ".env").write_text("not-a-secret", encoding="utf-8")
                request = json.loads(kwargs["input_text"])
                return process_result(json.dumps(response_for(request)))

            with patch("storyloop.stage_judges.run_process", side_effect=run):
                with self.assertRaises(StoryLoopError) as caught:
                    run_stage_judge(
                        project, config, "foundation", "core", {},
                        resolver=lambda name: "C:/python.exe",
                    )

        self.assertEqual(caught.exception.code, "WORKSPACE_TAINTED")
    def test_real_judge_filesystem_side_effect_is_rejected(self):
        config = loop_config()
        code = (
            "import json,sys; from pathlib import Path; r=json.load(sys.stdin); "
            "p=Path('.git/config'); p.parent.mkdir(); "
            "p.write_text('bad',encoding='utf-8'); "
            "print(json.dumps({'protocol':'loop.judge.v2','request_id':r['request_id'],"
            "'stage':r['stage'],'judge':r['judge'],'phase':r['phase'],'status':'pass',"
            "'summary':'fake pass','findings':[]}))"
        )
        config["stages"][0]["judges"][1]["command"] = [sys.executable, "-c", code]

        with tempfile.TemporaryDirectory() as folder:
            project = self.project(folder)
            with self.assertRaisesRegex(StoryLoopError, "modified the project"):
                run_stage_judge(project, config, "foundation", "core", {},
                                resolver=lambda name: sys.executable)
            self.assertTrue((project / ".git/config").exists())


    def test_real_judge_metadata_side_effect_is_rejected(self):
        config = loop_config()
        code = (
            "import json,os,stat,sys; r=json.load(sys.stdin); "
            "os.chmod('README.md',stat.S_IREAD); "
            "print(json.dumps({'protocol':'loop.judge.v2','request_id':r['request_id'],"
            "'stage':r['stage'],'judge':r['judge'],'phase':r['phase'],'status':'pass',"
            "'summary':'fake pass','findings':[]}))"
        )
        config["stages"][0]["judges"][1]["command"] = [sys.executable, "-c", code]

        with tempfile.TemporaryDirectory() as folder:
            project = self.project(folder)
            try:
                with self.assertRaisesRegex(StoryLoopError, "modified the project"):
                    run_stage_judge(project, config, "foundation", "core", {},
                                    resolver=lambda name: sys.executable)
            finally:
                (project / "README.md").chmod(0o600)


    def test_linked_git_worktree_fails_before_judge_execution(self):
        config = loop_config()
        with tempfile.TemporaryDirectory() as folder, \
                patch("storyloop.stage_judges.run_process") as run:
            project = self.project(folder)
            (project / ".git").write_text("gitdir: C:/outside/worktree", encoding="utf-8")

            with self.assertRaisesRegex(StoryLoopError, "Linked Git"):
                run_stage_judge(project, config, "foundation", "core", {},
                                resolver=lambda name: "C:/python.exe")

            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
