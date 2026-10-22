"""Agent operations injected into the run controller, without role context."""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class LoopServices:
    """Agent operations kept injectable so the controller remains authoritative."""

    generate_smoke: Callable[[Path, dict], dict]
    implement: Callable[[Path, dict], dict]
    review: Callable[[Path, dict], dict]
    characterize: Callable[[Path, dict], dict] | None = None
    design: Callable[[Path, dict], dict] | None = None
