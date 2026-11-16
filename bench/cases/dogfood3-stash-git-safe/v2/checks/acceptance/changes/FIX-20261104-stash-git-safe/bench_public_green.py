"""Frozen public-entry P2P selector under the trusted harness."""
import hashlib
from pathlib import Path
import types

SOURCE = 'test_public_run.py'
SHA256 = 'b067d8808920743e99ece22ee5ae93f951f9e5538e4bdc29b5f200e696db3102'
SELECTOR = 'PublicRunStop.test_repeat_base_stop_without_agent'


def load_tests(loader, tests, pattern):
    path = Path(__file__).with_name(SOURCE)
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != SHA256:
        raise RuntimeError('Frozen public-entry source changed')
    module = types.ModuleType('storyloop_frozen_public_entry')
    module.__file__ = str(path)
    exec(compile(content, str(path), 'exec'), module.__dict__)
    return loader.loadTestsFromName(SELECTOR, module)
