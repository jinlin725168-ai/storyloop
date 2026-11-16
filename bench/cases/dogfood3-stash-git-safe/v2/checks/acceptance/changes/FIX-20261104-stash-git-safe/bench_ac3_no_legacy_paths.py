"""Frozen selector for an unchanged acceptance source under the trusted harness."""
import hashlib
from pathlib import Path
import types

SOURCE = 'test_core.py'
SHA256 = 'f7655212ac1ea350e8f58e53db36914db7c261cf4b5823ce44c34e523a2cb927'
SELECTOR = 'NewBehavior.test_ac3_without_legacy_files_switches_stay_repo_bound'


def load_tests(loader, tests, pattern):
    path = Path(__file__).with_name(SOURCE)
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != SHA256:
        raise RuntimeError("Frozen acceptance source changed")
    module = types.ModuleType("storyloop_frozen_acceptance_source")
    module.__file__ = str(path)
    exec(compile(content, str(path), "exec"), module.__dict__)
    return loader.loadTestsFromName(SELECTOR, module)
