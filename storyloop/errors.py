"""Shared safe workflow errors used by foundation policy and validators."""


class StoryLoopError(RuntimeError):
    """A safe, actionable workflow failure with a machine-readable category."""

    def __init__(self, message: str, code: str = "BLOCKED",
                 details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = {} if details is None else details


def require(condition: bool, message: str) -> None:
    """Reject a failed deterministic check with the shared safe error type."""
    if not condition:
        raise StoryLoopError(message)
