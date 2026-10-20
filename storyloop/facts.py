"""Agent-stated code facts with safe locations, carried across a run (2a Q7).

The engine validates only that each cited location names a real source line. The
statement itself remains an agent claim rather than machine-verified truth.
"""

from __future__ import annotations

from pathlib import Path

from .evidence_locations import evidence_location_exists
from .failure_attribution import location_file

MAX_FACTS = 50
MAX_STATEMENT_LENGTH = 500


def merge_facts(project: Path, state: dict, facts: object, role: str) -> None:
    """Merge valid agent facts into state without persisting the state."""
    if not isinstance(facts, list):
        return
    record = [item for item in (state.get("facts") or []) if isinstance(item, dict)]
    known = {_fact_key(item) for item in record}
    known.discard(None)
    contract_commit = state.get("contract_commit")
    for item in facts:
        if not isinstance(item, dict):
            continue
        statement = item.get("statement")
        location = item.get("location")
        path = location_file(location)
        if not (isinstance(statement, str) and statement.strip()
                and len(statement.strip()) <= MAX_STATEMENT_LENGTH
                and isinstance(location, str) and path
                and (valid_location(project, location)
                     or (contract_commit and evidence_location_exists(project, location, contract_commit)))):
            continue
        key = (statement.strip(), path)
        if key in known:
            continue
        known.add(key)
        record.append({"statement": statement.strip(), "location": location.strip(),
                       "role": role, "attempt": state.get("attempt")})
    state["facts"] = record[-MAX_FACTS:]


def _fact_key(item: dict) -> tuple[str, str] | None:
    statement = item.get("statement")
    path = location_file(item.get("location"))
    if not isinstance(statement, str) or not path:
        return None
    return statement.strip(), path


def valid_location(project: Path, location: str) -> bool:
    """Return whether a location points to a safe existing source line."""
    return evidence_location_exists(project, location, None)
