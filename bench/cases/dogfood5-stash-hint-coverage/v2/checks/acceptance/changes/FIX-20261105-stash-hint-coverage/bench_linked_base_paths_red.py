"""Frozen selector for an unchanged acceptance source under the trusted harness."""
import hashlib
from pathlib import Path
import types

SOURCE = 'test_core.py'
SHA256 = 'e5481c6ed1b3f3f14d51943772c41e7effce6aaff97c7508751c3ba88c6e36e6'
SELECTOR = 'DefectReproduction'


def load_tests(loader, tests, pattern):
    path = Path(__file__).with_name(SOURCE)
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != SHA256:
        raise RuntimeError("Frozen acceptance source changed")
    module = types.ModuleType("storyloop_frozen_acceptance_source")
    module.__file__ = str(path)
    exec(compile(content, str(path), "exec"), module.__dict__)
    return loader.loadTestsFromName(SELECTOR, module)
