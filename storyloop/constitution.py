"""Single-source generation and identity for dual Agent constitutions."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .models import StoryLoopError, require
from .spec_gate import read_bounded_markdown, validate_proposal
from .stage_config import stage_order

MAX_CONSTITUTION_BYTES = 256 * 1024
DEFAULT_SOURCE = "governance/L1.md"


def generate_constitutions(project: Path,
                           source_path: str = DEFAULT_SOURCE) -> str:
    """Atomically generate byte-identical AGENTS.md and CLAUDE.md files."""
    project = Path(project).resolve()
    raw = read_bounded_markdown(project, source_path, MAX_CONSTITUTION_BYTES)
    require(bool(raw.decode("utf-8-sig").strip()), "L1 constitution must not be empty")
    for name in ("AGENTS.md", "CLAUDE.md"):
        _atomic_bytes(project / name, raw)
    return hashlib.sha256(raw).hexdigest()


def verify_constitutions(project: Path,
                         source_path: str = DEFAULT_SOURCE) -> str:
    """Verify both generated files exactly match the bounded L1 source."""
    project = Path(project).resolve()
    source = read_bounded_markdown(project, source_path, MAX_CONSTITUTION_BYTES)
    agents = read_bounded_markdown(project, "AGENTS.md", MAX_CONSTITUTION_BYTES)
    claude = read_bounded_markdown(project, "CLAUDE.md", MAX_CONSTITUTION_BYTES)
    require(source == agents == claude,
            "AGENTS.md and CLAUDE.md must match the L1 constitution")
    return hashlib.sha256(source).hexdigest()


def governance_identity(project: Path, config: dict,
                        source_path: str = DEFAULT_SOURCE,
                        required: bool = True) -> dict:
    """Return proposal/constitution digests, gating partially present inputs."""
    require(type(required) is bool, "Governance required flag must be boolean")
    require(type(required) is bool, "Governance required flag must be boolean")
    project = Path(project).resolve()
    paths = []
    for stage in stage_order(config):
        for path in stage["specs"]:
            if path not in paths:
                paths.append(path)
    signal = any((project / Path(path)).exists() for path in paths)
    signal = signal or any((project / name).exists()
                           for name in (source_path, "AGENTS.md", "CLAUDE.md"))
    if not required and not signal:
        return {"proposal_digests": [{"path": path, "digest": None} for path in paths],
                "constitution_digest": None}
    proposals = [validate_proposal(project, path) for path in paths]
    constitution = verify_constitutions(project, source_path)
    return {"proposal_digests": [{"path": item["path"], "digest": item["digest"]}
                                  for item in proposals],
            "constitution_digest": constitution}


def _atomic_bytes(path: Path, content: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except FileExistsError as exc:
        raise StoryLoopError("A prior constitution write is incomplete") from exc
    except OSError as exc:
        raise StoryLoopError("Cannot write generated constitution") from exc
