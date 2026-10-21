"""Bounded Git diagnostics must reclaim descendants before releasing the repository."""
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from storyloop.report_git import _git


@unittest.skipUnless(sys.platform == 'linux', 'Executable shim without platform shell wrappers')
class GitCaptureTreeTests(unittest.TestCase):
    def test_output_budget_reclaims_descendant_before_return(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            tools = root / 'tools'
            tools.mkdir()
            child = ("import os,time;from pathlib import Path;"
                     "Path('child.pid').write_text(str(os.getpid()));"
                     "time.sleep(.4);Path('escaped').write_text('late');time.sleep(10)")
            shim = ('#!' + sys.executable + '\nimport subprocess,sys,time\n'
                    + 'from pathlib import Path\n'
                    + 'subprocess.Popen([sys.executable,"-c",' + repr(child) + '])\n'
                    + 'deadline=time.monotonic()+5\n'
                    + 'while not Path("child.pid").exists() and time.monotonic()<deadline: time.sleep(.01)\n'
                    + 'sys.stdout.write("x"*10000);sys.stdout.flush();time.sleep(10)\n')
            binary = tools / 'git'
            binary.write_text(shim)
            binary.chmod(0o700)
            try:
                with patch.dict(os.environ, {'PATH': str(tools) + os.pathsep + os.defpath}):
                    result = _git(root, time.monotonic()+5, 2, 'show')
                self.assertTrue(result['limited'])
                self.assertLessEqual(len(result['stdout'].encode()), 2)
                time.sleep(.6)
                self.assertFalse((root/'escaped').exists(), 'A descendant survived bounded Git capture')
            finally:
                # This cleanup also runs on RED, where the old parent-only kill leaks it.
                if (root/'child.pid').exists():
                    try:
                        os.kill(int((root/'child.pid').read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass


class BoundedCaptureProcessTests(unittest.TestCase):
    def test_budget_stops_tree_before_releasing_working_directory(self):
        from storyloop.report_git import _capture
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            workspace = root / 'workspace'
            workspace.mkdir()
            child = ("import time;from pathlib import Path;Path('ready').touch();"
                     "time.sleep(.4);Path('escaped').touch();time.sleep(10)")
            parent = ("import subprocess,sys,time;from pathlib import Path;"
                      + 'subprocess.Popen([sys.executable,"-c",' + repr(child) + ']);'
                      + 'deadline=time.monotonic()+5\n'
                      + 'while not Path("ready").exists() and time.monotonic()<deadline: time.sleep(.01)\n'
                      + 'sys.stdout.write("x"*10000);sys.stdout.flush();time.sleep(10)')
            result = _capture(workspace, time.monotonic()+8, 2, [sys.executable, '-c', parent])
            self.assertTrue(result['limited'], result)
            self.assertEqual(result['stdout'], 'xx')
            released = root / 'released'
            workspace.rename(released)  # Windows rejects this if a descendant still owns the cwd.
            time.sleep(.6)
            self.assertFalse((released/'escaped').exists())

    def test_deadline_closes_process_and_does_not_start_after_expiry(self):
        from storyloop.report_git import _capture
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = _capture(root, time.monotonic()+.2, 2,
                              [sys.executable, '-c', 'import time;time.sleep(10)'])
            self.assertTrue(result['limited'])
            root.rename(root.with_name(root.name+'-released'))
            root.with_name(root.name+'-released').rename(root)
            with patch('storyloop.report_git._start_process') as start:
                result = _capture(root, time.monotonic()-1, 2, ['unavailable'])
            start.assert_not_called()
            self.assertIsNone(result['returncode'])


class CaptureLifecycleTests(unittest.TestCase):
    def test_timer_start_failure_still_finalizes_owned_process(self):
        from storyloop.report_git import _capture
        owner = MagicMock()
        timer = MagicMock()
        timer.start.side_effect = RuntimeError('thread unavailable')
        with patch('storyloop.report_git._CaptureProcess', return_value=owner), \
                patch('storyloop.report_git.threading.Timer', return_value=timer):
            try:
                _capture(Path.cwd(), time.monotonic()+2, 2, ['git'])
            except RuntimeError:
                pass  # RED must expose the missing cleanup, not just an exception.
        owner.finish.assert_called_once()
        owner.process.stdout.close.assert_called_once()
        timer.join.assert_not_called()

    def test_timer_uses_remaining_absolute_budget_after_startup(self):
        from storyloop.report_git import _capture
        clock = [10.0]
        owner = MagicMock()
        owner.process.stdout.read.return_value = b'x'
        owner.process.returncode = 0
        owner.finish.return_value = True
        def startup(*args):
            clock[0] = 10.4
            return owner
        with patch('storyloop.report_git.time.monotonic', side_effect=lambda: clock[0]), \
                patch('storyloop.report_git._CaptureProcess', side_effect=startup), \
                patch('storyloop.report_git.threading.Timer') as timer:
            result = _capture(Path.cwd(), 11.0, 2, ['git'])
        self.assertAlmostEqual(timer.call_args.args[0], .6)
        self.assertEqual(result['stdout'], 'x')
