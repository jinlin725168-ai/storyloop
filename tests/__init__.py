
"""Test process fixtures shared by direct unittest and suite-runner invocations."""

import atexit
import os
from pathlib import Path
import tempfile


_AGENT_TEMP = tempfile.TemporaryDirectory(prefix="storyloop-test-agent-")
AGENT_TEMP_ROOT = Path(_AGENT_TEMP.name)
os.environ["STORYLOOP_AGENT_WORKTREE_TEMP_ROOT"] = str(AGENT_TEMP_ROOT)
atexit.register(_AGENT_TEMP.cleanup)
