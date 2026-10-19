"""Use an explicit Python fake CLI on every OS; keep real adapter/subprocess code."""
import importlib.util
from pathlib import Path
import sys

source = Path(__file__).resolve().parents[3] / 'examples/claude-code/claude_agent.py'
sys.path.insert(0, str(source.parents[2]))
spec = importlib.util.spec_from_file_location('claude_adapter', source)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
original = adapter._base_command
adapter.shutil.which = lambda name: sys.executable
adapter._base_command = lambda *args: [sys.executable, str(Path(__file__).with_name('claude.py')), *original(*args)[1:]]
profile_original = adapter.profile_command
adapter.profile_command = lambda *args: [sys.executable, str(Path(__file__).with_name('claude.py')), *profile_original(*args)[1:]]
raise SystemExit(adapter.main())
