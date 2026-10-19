"""Internal invocation context; adapter replies cannot request a stronger or weaker tier."""
from contextlib import contextmanager
from contextvars import ContextVar

_VERIFY = ContextVar('review_verify', default=False)


@contextmanager
def strong_verification():
    """Bind the engine-owned strong override for a single verification call."""
    token = _VERIFY.set(True)
    try:
        yield
    finally:
        _VERIFY.reset(token)


def verification_active():
    return _VERIFY.get()
