"""Checked, positive-only routing facts supplied by implement and review roles."""
from pathlib import Path

from .evidence_locations import evidence_location_exists
from .models import require
from .routing_policy import RISK_FIELDS


def validate_routing_risks(value: object) -> None:
    """Validate optional typed assertions without inferring risks from free-form text."""
    if value is None:
        return
    require(isinstance(value, list) and len(value) <= 50, 'Invalid routing_risks list')
    for item in value:
        require(isinstance(item, dict) and set(item) == {'risk', 'statement', 'location'},
                'Invalid routing_risks entry')
        require(item['risk'] in RISK_FIELDS, 'Unknown routing risk')
        require(isinstance(item['statement'], str) and 0 < len(item['statement'].strip()) <= 500,
                'Routing risk statement must be bounded nonempty text')
        require(isinstance(item['location'], str) and 0 < len(item['location']) <= 1000,
                'Routing risk needs a source location')


def merge_routing_risks(project: Path, state: dict, value: object, role: str) -> None:
    """Persist only an entirely valid batch with role/attempt provenance; never erase yes."""
    validate_routing_risks(value)
    if not value:
        return
    require(state.get('routing', {}).get('version') == 1, 'Routing risks require a routed run')
    entries = []
    for item in value:
        require(evidence_location_exists(project, item['location'], state.get('contract_commit')),
                'Routing risk location does not exist: ' + item['location'])
        entries.append({**item, 'role': role, 'attempt': state.get('attempt')})
    records = state['routing'].setdefault('risk_updates', [])
    known = {(r['risk'], r['statement'], r['location']) for r in records}
    for item in entries:
        key = (item['risk'], item['statement'], item['location'])
        if key not in known:
            records.append(item)
            known.add(key)
