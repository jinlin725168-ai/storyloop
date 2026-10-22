"""Deterministic lifecycle checks; G1–G3 move here in Tasks 4 and 5."""
import hashlib
from pathlib import Path
from .models import require
from .smoke import RED_EVIDENCE
from .storage import load_json


def guard_updates(value: dict) -> None:
    """A caller or delivery effect cannot smuggle a second status assignment."""
    require(isinstance(value, dict) and "status" not in value,
            "Transition updates must be a dictionary without status")


def read_red_evidence(project: Path, change_id: str) -> dict:
    """Preserve the existing cache check: identity/status only, not new fingerprint policy."""
    path = project / ".storyloop/evidence" / change_id / RED_EVIDENCE
    require(path.is_file(), "RED evidence is missing")
    value = load_json(path)
    require(value.get("change_id") == change_id and value.get("status") == "RED",
            "Stored RED evidence is invalid")
    return {**value, "evidence_path": str(path),
            "evidence_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def guard_g1(project: Path, state: dict, context: dict) -> None:
    """Fresh RED is judged by the controlled runner; cached RED keeps its old weak check."""
    require(bool(state.get("contract_commit")), "Contract must be committed before implementation")
    read_red_evidence(project, state["change_id"])
