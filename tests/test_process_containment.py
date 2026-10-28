"""System-level guarantees for process-tree containment."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from storyloop.models import StoryLoopError
from storyloop.processutil import (
    MAX_OUTPUT,
    _join_feeder,
    _start_windows,
    _stop_tree,
    run_process,
    redact,
)


class RedactPerformanceTests(unittest.TestCase):
    def test_url_userinfo_and_existing_labels_keep_their_output(self):
        cases = {
            "https://alice:p:a:s:s@example.test/path": "https://[REDACTED]@example.test/path",
            "s3://:alice:pass@bucket/key": "s3://[REDACTED]@bucket/key",
            "s3://alice:pass:@bucket/key": "s3://[REDACTED]@bucket/key",
            "https://alice@example.test/path": "https://alice@example.test/path",
            "token=synthetic-value": "token=[REDACTED]",
            "plain/path/abc123": "plain/path/abc123",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(redact(value), expected)

    def test_adversarial_url_and_json_finish_within_two_seconds(self):
        script = (
            "from storyloop.processutil import redact\n"
            "import sys\n"
            "value = ('s3://' + 'a:' * 100000) if sys.argv[1] == 'url' "
            "else (('x' * 200000) if sys.argv[1] == 'plain' "
            "else ('{\"url\":\"http://localhost:8080\",\"k\":\"v\"' + ',\"k\":\"v\"' * 150000 + '}'))\n"
            "redact(value)\n"
        )
        for sample in ("url", "json", "plain"):
            with self.subTest(sample=sample):
                result = subprocess.run(
                    [sys.executable, "-c", script, sample], cwd=Path(__file__).resolve().parents[1],
                    capture_output=True, text=True, timeout=2, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_existing_post_redaction_limits_are_applied_before_scanning(self):
        from storyloop.call_log import close_call, open_call
        from storyloop.project_loop import _hand_back_attempt

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            call_id = open_call(root, "review", None, "command", {})
            error = "x" * 3975 + " http://alice:synthetic-secret@example.test"
            with patch("storyloop.call_log.redact", wraps=redact) as scanner:
                close_call(root, call_id, status="ERROR", wall_ms=1, error=error)
            self.assertLessEqual(len(scanner.call_args.args[0]), 4000)
            self.assertNotIn("alice:synthetic", (root / ".storyloop/state/telemetry/calls"
                                                    / call_id / "meta.json").read_text(encoding="utf-8"))

        state = {"attempt": 1, "change_id": "FEAT-1"}
        error = StoryLoopError("x" * 575 + " http://alice:synthetic-secret@example.test",
                               code="AGENT_UNAVAILABLE")
        with patch("storyloop.project_loop.redact", wraps=redact) as scanner, \
                patch("storyloop.project_loop.save_run"), \
                patch("storyloop.stop_report.build_stop", return_value={}):
            _hand_back_attempt(Path.cwd(), state, error)
        self.assertLessEqual(len(scanner.call_args_list[0].args[0]), 600)
        self.assertNotIn("alice:synthetic", state["agent_failures"][0]["message"])



class ProcessContainmentTests(unittest.TestCase):
    def test_posix_group_cleanup_never_claims_security_containment(self):
        process = MagicMock(pid=123)
        process.poll.return_value = 0

        with patch("storyloop.processutil.os.name", "posix"), \
                patch("storyloop.processutil.os.killpg", create=True), \
                patch("storyloop.processutil.signal.SIGKILL", 9, create=True):
            stopped, contained = _stop_tree(process, None)

        self.assertIs(stopped, True)
        self.assertIs(contained, False)

    @unittest.skipUnless(sys.platform == "linux", "Linux subreaper")
    def test_detached_descendant_is_reaped_on_success_and_timeout(self):
        for timeout in (0.5, 5):
            with self.subTest(timeout=timeout), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                marker = root / "escaped.txt"
                ready = root / "ready.txt"
                child = (
                    "import os,time; from pathlib import Path; os.setsid(); "
                    f"Path({str(ready)!r}).write_text('ready'); time.sleep(1); "
                    f"Path({str(marker)!r}).write_text('escaped')"
                )
                parent = (
                    "import subprocess,sys,time; from pathlib import Path; "
                    f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
                    f"ready=Path({str(ready)!r})\n"
                    "while not ready.exists(): time.sleep(0.01)\n"
                    + ("time.sleep(5)" if timeout < 1 else "")
                )
                result = run_process([sys.executable, "-c", parent], root, timeout)
                self.assertTrue(ready.exists(), result)
                self.assertTrue(result["contained"], result)
                self.assertEqual(result["timed_out"], timeout < 1)
                time.sleep(1.1)
                self.assertFalse(marker.exists())

    @unittest.skipUnless(sys.platform == "linux", "Linux subreaper")
    def test_linux_supervisor_without_completion_fails_closed(self):
        from storyloop.linux_job import LinuxJob
        reader, writer = os.pipe()
        os.close(writer)
        process = MagicMock()
        process.poll.return_value = 0
        stopped, contained = _stop_tree(process, LinuxJob(reader))
        self.assertFalse(stopped)
        self.assertFalse(contained)

    @unittest.skipUnless(sys.platform == "linux", "Linux subreaper")
    def test_linux_subreaper_setup_failure_never_launches_command(self):
        from storyloop.linux_supervisor import main
        reader, writer = os.pipe()
        try:
            with patch("sys.argv", ["supervisor", str(writer), "untrusted"]), \
                    patch("storyloop.linux_supervisor.ctypes.CDLL") as library, \
                    patch("storyloop.linux_supervisor.subprocess.Popen") as launch:
                library.return_value.prctl.return_value = -1
                self.assertEqual(main(), 125)
                launch.assert_not_called()
        finally:
            os.close(reader)
            os.close(writer)

    def test_feeder_start_failure_stops_assigned_job(self):
        process = MagicMock(pid=123, stdin=io.BytesIO())
        job = MagicMock()
        feeder = MagicMock()
        feeder.start.side_effect = RuntimeError("thread unavailable")
        feeder.is_alive.return_value = False

        with patch("storyloop.processutil.subprocess.CREATE_NEW_PROCESS_GROUP", 512, create=True), \
                patch("storyloop.windows_job.WindowsJob", return_value=job), \
                patch("storyloop.processutil.subprocess.Popen", return_value=process), \
                patch("storyloop.processutil.threading.Thread", return_value=feeder), \
                patch("storyloop.processutil._stop_tree", return_value=(True, True)) as stop_tree:
            with self.assertRaises(RuntimeError) as caught:
                _start_windows([sys.executable, "-c", "pass"], Path.cwd(), {},
                               io.BytesIO(), io.BytesIO(), io.BytesIO())

        self.assertNotIsInstance(caught.exception, StoryLoopError)
        stop_tree.assert_called_once_with(process, job)

    def test_fallback_kill_failure_still_waits_and_fails_closed(self):
        process = MagicMock()
        process.poll.return_value = None
        process.kill.side_effect = OSError("kill failed")
        job = MagicMock()

        with patch("storyloop.processutil.os.name", "nt"):
            stopped, contained = _stop_tree(process, job)

        process.wait.assert_called_once_with(timeout=10)
        self.assertIs(stopped, False)
        self.assertIs(contained, False)

    def test_cleanup_exceptions_continue_best_effort_and_fail_closed(self):
        process = MagicMock()
        process.poll.return_value = None
        job = MagicMock()
        job.terminate.side_effect = RuntimeError("terminate interrupted")

        with patch("storyloop.processutil.os.name", "nt"):
            stopped, contained = _stop_tree(process, job)

        self.assertIs(stopped, False)
        self.assertIs(contained, False)
        job.close.assert_called_once_with()
        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=10)

    def test_feeder_join_exception_returns_unsafe_containment(self):
        feeder = MagicMock()
        feeder.join.side_effect = RuntimeError("join interrupted")

        stopped, contained = _join_feeder(feeder, True, True)

        self.assertIs(stopped, False)
        self.assertIs(contained, False)

    def test_output_limit_is_rechecked_after_tree_cleanup(self):
        process = MagicMock(returncode=0)
        output_handle = None

        def start(command, cwd, env, source, output, error):
            nonlocal output_handle
            output_handle = output
            return process, MagicMock(), None

        def stop_tree(current, job):
            output_handle.write(b"x" * (MAX_OUTPUT + 1))
            output_handle.flush()
            return True, True

        with patch("storyloop.processutil._start_process", side_effect=start), \
                patch("storyloop.processutil._wait", return_value=(False, False)), \
                patch("storyloop.processutil._stop_tree", side_effect=stop_tree):
            result = run_process([sys.executable, "-c", "pass"], Path.cwd(), 5)

        self.assertIs(result["truncated"], True)
        self.assertEqual(len(result["stdout"]), MAX_OUTPUT)

    def test_interruption_stops_tree_and_joins_stdin_feeder(self):
        process = MagicMock()
        job = MagicMock()
        feeder = MagicMock()
        feeder.is_alive.return_value = False

        with patch("storyloop.processutil._start_process",
                   return_value=(process, job, feeder)), \
                patch("storyloop.processutil._wait", side_effect=KeyboardInterrupt), \
                patch("storyloop.processutil._stop_tree",
                      return_value=(True, True)) as stop_tree:
            with self.assertRaises(KeyboardInterrupt):
                run_process([sys.executable, "-c", "pass"], Path.cwd(), 5)

        stop_tree.assert_called_once_with(process, job)
        feeder.join.assert_called_once_with(timeout=10)

    def test_wait_error_with_failed_cleanup_reports_unsafe_containment(self):
        process = MagicMock()
        job = MagicMock()

        with patch("storyloop.processutil._start_process",
                   return_value=(process, job, None)), \
                patch("storyloop.processutil._wait", side_effect=RuntimeError("wait failed")), \
                patch("storyloop.processutil._stop_tree", return_value=(False, False)):
            with self.assertRaises(StoryLoopError) as caught:
                run_process([sys.executable, "-c", "pass"], Path.cwd(), 5)

        self.assertEqual(caught.exception.code, "UNSAFE_CONTAINMENT")

    def test_job_assignment_failure_stops_the_blocked_bootstrap(self):
        process = MagicMock(pid=123, stdin=io.BytesIO())
        job = MagicMock()
        job.assign.side_effect = OSError("assignment failed")

        with patch("storyloop.processutil.subprocess.CREATE_NEW_PROCESS_GROUP", 512, create=True), \
                patch("storyloop.windows_job.WindowsJob", return_value=job), \
                patch("storyloop.processutil.subprocess.Popen", return_value=process):
            with self.assertRaises(OSError):
                _start_windows([sys.executable, "-c", "pass"], Path.cwd(), {},
                               io.BytesIO(), io.BytesIO(), io.BytesIO())

        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=10)
        job.close.assert_called_once_with()

    def test_job_assignment_runtime_error_stops_blocked_bootstrap(self):
        process = MagicMock(pid=123, stdin=io.BytesIO())
        job = MagicMock()
        job.assign.side_effect = RuntimeError("assignment interrupted")

        with patch("storyloop.processutil.subprocess.CREATE_NEW_PROCESS_GROUP", 512, create=True), \
                patch("storyloop.windows_job.WindowsJob", return_value=job), \
                patch("storyloop.processutil.subprocess.Popen", return_value=process):
            with self.assertRaises(RuntimeError) as caught:
                _start_windows([sys.executable, "-c", "pass"], Path.cwd(), {},
                               io.BytesIO(), io.BytesIO(), io.BytesIO())

        self.assertNotIsInstance(caught.exception, StoryLoopError)
        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=10)
        job.close.assert_called_once_with()

    def test_successful_parent_cannot_leave_a_descendant_writer(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            marker = root / "late-success.txt"
            child = (
                "import time; from pathlib import Path; time.sleep(1); "
                f"Path({str(marker)!r}).write_text('late', encoding='utf-8')"
            )
            parent = (
                "import subprocess,sys; "
                f"subprocess.Popen([sys.executable, '-c', {child!r}])"
            )

            result = run_process([sys.executable, "-c", parent], root, 5)

            self.assertEqual(result["returncode"], 0)
            self.assertFalse(result["timed_out"])
            self.assertIs(result["contained"], True)
            time.sleep(1.2)
            self.assertFalse(marker.exists(),
                             "A descendant must not write after its parent reports success")

    def test_nonzero_parent_cannot_leave_a_descendant_writer(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            marker = root / "late-failure.txt"
            child = (
                "import time; from pathlib import Path; time.sleep(1); "
                f"Path({str(marker)!r}).write_text('late', encoding='utf-8')"
            )
            parent = (
                "import subprocess,sys; "
                f"subprocess.Popen([sys.executable, '-c', {child!r}]); sys.exit(7)"
            )

            result = run_process([sys.executable, "-c", parent], root, 5)

            self.assertEqual(result["returncode"], 7)
            time.sleep(1.2)
            self.assertFalse(marker.exists())

    def test_successful_parent_cannot_leave_a_grandchild_writer(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            marker = root / "late-grandchild.txt"
            grandchild = (
                "import time; from pathlib import Path; time.sleep(1); "
                f"Path({str(marker)!r}).write_text('late', encoding='utf-8')"
            )
            child = (
                "import subprocess,sys; "
                f"subprocess.Popen([sys.executable, '-c', {grandchild!r}])"
            )
            parent = (
                "import subprocess,sys; "
                f"subprocess.Popen([sys.executable, '-c', {child!r}])"
            )

            result = run_process([sys.executable, "-c", parent], root, 5)

            self.assertEqual(result["returncode"], 0)
            time.sleep(1.2)
            self.assertFalse(marker.exists())

    def test_output_limit_closes_descendant_tree(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            marker = root / "late-output.txt"
            child = (
                "import time; from pathlib import Path; time.sleep(1); "
                f"Path({str(marker)!r}).write_text('late', encoding='utf-8')"
            )
            parent = (
                "import subprocess,sys,time; "
                f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
                "sys.stdout.write('x' * (1024 * 1024 + 1)); "
                "sys.stdout.flush(); time.sleep(5)"
            )

            result = run_process([sys.executable, "-c", parent], root, 5)

            self.assertTrue(result["truncated"])
            time.sleep(1.2)
            self.assertFalse(marker.exists())

    def test_timeout_releases_child_working_directory_before_return(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            workspace = root / "workspace"
            workspace.mkdir()
            result = run_process(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                workspace,
                1,
            )
            released = root / "released"
            workspace.rename(released)

        self.assertTrue(result["timed_out"])
        self.assertTrue(result["contained"])

    def test_bootstrap_preserves_stdin_and_stdout_protocol(self):
        with tempfile.TemporaryDirectory() as folder:
            command = [sys.executable, "-c",
                       "import sys; sys.stdout.write(sys.stdin.read())"]

            result = run_process(command, Path(folder), 5, input_text="协议输入")

            self.assertEqual(result["returncode"], 0)
            self.assertEqual(result["stdout"], "协议输入")
            self.assertEqual(result["error"], "")


if __name__ == "__main__":
    unittest.main()






