"""Frozen selector for an unchanged acceptance source under the trusted harness."""
import hashlib
from pathlib import Path
import types

SOURCE = 'test_findings.py'
SHA256 = '167b343746fba540b486244c2e9d93a1ca6d25a5358624f45be41e6c9097b29f'
SELECTOR = 'ExistingBehavior'


def load_tests(loader, tests, pattern):
    path = Path(__file__).with_name(SOURCE)
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != SHA256:
        raise RuntimeError("Frozen acceptance source changed")
    module = types.ModuleType("storyloop_frozen_acceptance_source")
    module.__file__ = str(path)
    exec(compile(content, str(path), "exec"), module.__dict__)
    return loader.loadTestsFromName(SELECTOR, module)
