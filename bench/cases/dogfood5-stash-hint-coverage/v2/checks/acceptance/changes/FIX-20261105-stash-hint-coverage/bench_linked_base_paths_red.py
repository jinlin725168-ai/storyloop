"""Frozen selector for an unchanged acceptance source under the trusted harness."""
import hashlib
from pathlib import Path
import types

SOURCE = 'test_core.py'
SHA256 = 'cdfa73ea1a491ef6088ab24b6900f5a84ed29b7202c82b8358af988b5316ad9c'
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
