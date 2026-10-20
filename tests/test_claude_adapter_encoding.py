"""The Claude adapter speaks UTF-8 on every pipe, whatever the Windows locale encoding is."""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ADAPTER = Path(__file__).resolve().parents[1] / "examples/claude-code/claude_agent.py"
SPEC = importlib.util.spec_from_file_location("claude_agent_encoding", ADAPTER)
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)

REQUEST = {"role": "implement", "project": "/tmp/p", "payload": {"goal": "预订会议室，不允许重叠"},
           "instructions": "按合同实现"}
ENVELOPE = json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": "s",
                       "result": '{"summary":"完成","questions":[]}', "total_cost_usd": 0.0, "num_turns": 1})


class _Completed:
    def __init__(self, stdout):
        self.stdout, self.stderr, self.returncode = stdout, "", 0


class ClaudeAdapterEncodingTests(unittest.TestCase):
    def test_claude_subprocess_pipes_are_utf8(self):
        seen = []

        def fake_run(command, **kwargs):
            seen.append(kwargs)
            return _Completed(ENVELOPE)

        with patch.object(adapter.subprocess, "run", fake_run), \
                patch.object(adapter.shutil, "which", return_value="/usr/bin/claude"), \
                patch("sys.stdin", io.StringIO(json.dumps(REQUEST, ensure_ascii=False))), \
                patch("sys.stdout", new_callable=io.StringIO), \
                patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(adapter.main_with_args([]), 0)
        self.assertTrue(seen)
        self.assertTrue(all(kwargs.get("encoding") == "utf-8" for kwargs in seen))

    @unittest.skipUnless(os.name == "posix", "the fake claude on PATH is a POSIX script")
    def test_request_is_read_as_utf8_under_a_non_utf8_locale(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = Path(folder) / "claude"
            fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake.chmod(0o755)
            body = json.dumps(REQUEST, ensure_ascii=False).encode("utf-8")

            def prompt_chars(encoding):
                env = {**os.environ, "PATH": folder + os.pathsep + os.environ.get("PATH", ""),
                       "PYTHONIOENCODING": encoding, "PYTHONUTF8": "0"}
                done = subprocess.run([sys.executable, str(ADAPTER), "--dry-run"], input=body,
                                      capture_output=True, env=env, check=True)
                return json.loads(done.stdout)["prompt_chars"]

            # latin-1 stands in for a Windows code page: it would split each CJK character into three.
            self.assertEqual(prompt_chars("latin-1"), prompt_chars("utf-8"))


if __name__ == "__main__":
    unittest.main()
