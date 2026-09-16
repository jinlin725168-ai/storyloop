"""Authoritative bindings for non-authoritative Stage audit projections."""

from __future__ import annotations

import re

from .models import fields, require

DIGEST = re.compile(r"[0-9a-f]{64}")


def validate_artifact_chain(state: dict) -> None:
    """Validate a bounded, append-only sequence of attempt digests."""
    chain = state["artifact_chain"]
    require(isinstance(chain, list) and len(chain) <= 100,
            "Invalid artifact chain")
    previous = 0
    for entry in chain:
        fields(entry, {"attempt", "digest"})
        attempt = entry["attempt"]
        require(type(attempt) is int and previous < attempt <= state["attempts_started"],
                "Invalid artifact chain attempt")
        require(isinstance(entry["digest"], str) and DIGEST.fullmatch(entry["digest"]),
                "Invalid artifact chain digest")
        previous = attempt


def bind_artifact(state: dict, digest: str) -> dict:
    """Append the current attempt digest, idempotently and without rewriting history."""
    validate_artifact_chain(state)
    require(isinstance(digest, str) and DIGEST.fullmatch(digest),
            "Invalid artifact digest")
    attempt = state["attempts_started"]
    require(type(attempt) is int and attempt > 0, "Artifact needs an attempt")
    matches = [item for item in state["artifact_chain"]
               if item["attempt"] == attempt]
    if matches:
        require(matches[0]["digest"] == digest, "Run artifact was tampered")
        return state
    require(not state["artifact_chain"]
            or state["artifact_chain"][-1]["attempt"] < attempt,
            "Artifact chain cannot be rewritten")
    return {**state, "artifact_chain": [
        *state["artifact_chain"], {"attempt": attempt, "digest": digest},
    ]}
