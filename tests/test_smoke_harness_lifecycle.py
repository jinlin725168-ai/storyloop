import http.server
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

from storyloop.models import StoryLoopError
from storyloop.testing.smoke_harness import v1
from tests.test_smoke_harness import fixture, invoke


class LifecycleTests(unittest.TestCase):
    def test_completed_hooks_execute_only_selected_case_and_cleanup_error_dominates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            script = root / "acceptance/tools/python_smoke.py"
            script.write_text('''from storyloop.testing.smoke_harness.v1 import python_cases, run_hooks

def phase(context):
    return None

def run_case(context, case):
    return python_cases(context.project, [case])

def run(context):
    return run_hooks(context, build=phase, start=phase, ready=phase,
                     run_case=run_case, cleanup=phase)
''')
            self.assertEqual(invoke(root, "cases/a.py")["status"], "PASS")
            script.write_text(script.read_text().replace("cleanup=phase", "cleanup=broken") +
                              "\ndef broken(context):\n    raise OSError('private cleanup detail')\n")
            result = invoke(root, "cases/b.py")
            self.assertEqual(result.get("returncode"), 2, result)
            self.assertNotIn("private cleanup detail", str(result))

    def test_pending_hooks_are_rejected_before_build_and_cleanup_runs_after_failure(self):
        self.assertTrue(hasattr(v1, "run_hooks"), "Missing explicit hook lifecycle")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            manifest = json.loads((root / "acceptance/onboarding.json").read_text())
            config = manifest["executors"]["acceptance/tools/python_smoke.py"]
            events = []
            def phase(context):
                events.append("phase")
            def cleanup(context):
                events.append("cleanup")
            def fail(context):
                events.append("build")
                raise OSError("build failed")
            config["hooks"]["ready"]["status"] = "pending"
            context = v1.Context(root, ["cases/a.py"], config)
            result = v1.run_hooks(context, build=phase, start=phase, ready=phase,
                                  run_case=phase, cleanup=cleanup)
            self.assertEqual(result.code, 2)
            self.assertEqual(events, [])
            config["hooks"]["ready"]["status"] = "implemented"
            result = v1.run_hooks(context, build=fail, start=phase, ready=phase,
                                  run_case=phase, cleanup=cleanup)
            self.assertEqual(result.code, 2)
            self.assertEqual(events, ["build", "cleanup"])

    def test_http_probe_requires_declared_success_status(self):
        self.assertTrue(hasattr(v1, "wait_http"), "Missing explicit health probe")
        class Handler(http.server.BaseHTTPRequestHandler):
            status = 503
            def do_GET(self):
                self.send_response(self.status)
                self.end_headers()
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/health"
            for status in (404, 503):
                Handler.status = status
                with self.assertRaises(StoryLoopError):
                    v1.wait_http(url, timeout=.1)
            Handler.status = 204
            v1.wait_http(url, expected_status=204, timeout=1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_native_commands_keep_environment_allowlist_and_reject_shell_wrappers(self):
        self.assertTrue(hasattr(v1, "run_command"), "Missing controlled argv helper")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = root / "environment.py"
            script.write_text("import os\nassert not {'HOME','PYTHONPATH','AWS_ACCESS_KEY_ID'} & set(os.environ)\n")
            outcome = v1.run_command([sys.executable, str(script)], root)
            self.assertEqual(outcome["returncode"], 0)
            for argv in ("echo passed", ["bash", "-c", "echo passed"], ["run.cmd"], ["env", "sh"]):
                with self.subTest(argv=argv), self.assertRaises(StoryLoopError):
                    v1.run_command(argv, root)

    def test_service_close_reclaims_child_tree(self):
        self.assertTrue(hasattr(v1, "Service"), "Missing managed service lifecycle")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            command = [sys.executable, "-c", "import time; time.sleep(60)"]
            service = v1.Service(command, root)
            service.close()
            self.assertIsNotNone(service.process.poll())
            self.assertTrue(service.contained)
