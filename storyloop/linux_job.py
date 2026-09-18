"""Controller side of the Linux subreaper completion protocol."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


class LinuxJob:
    def __init__(self, completion: int):
        self.completion = completion

    def finish(self, process) -> bool:
        try:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
            os.set_blocking(self.completion, False)
            return os.read(self.completion, 2) == b"1"
        finally:
            os.close(self.completion)


def start_linux(command, cwd, env, source, output, error):
    reader, writer = os.pipe()
    try:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name("linux_supervisor.py")),
             str(writer), *command],
            cwd=cwd, env=env, stdin=source, stdout=output, stderr=error,
            shell=False, start_new_session=True, pass_fds=(writer,),
        )
    except BaseException:
        os.close(reader)
        raise
    finally:
        os.close(writer)
    return process, LinuxJob(reader), None
