"""Small bounded presentation primitives shared by reader sections."""
from .report_evidence import _bounded


def item(identifier, data=None, source='machine', reason=None, availability=None):
    """Build an explicitly sourced item; absent data never means an empty collection."""
    data, truncated = _bounded(data)
    status = availability or ('AVAILABLE' if data is not None else 'NOT_AVAILABLE')
    result = {'id': identifier, 'source': source, 'availability': status, 'data': data}
    if reason or truncated or data is None:
        result['reason'] = reason or ('SNAPSHOT_TRUNCATED' if truncated else 'SOURCE_NOT_PERSISTED')
    return result


def effective_tier(change, report, evidence):
    """Use persisted effective routing before initial intake, retaining unknowns."""
    routing = (evidence.get('run') or {}).get('routing') or {}
    return (routing.get('effective_tier') or (report.get('routing') or {}).get('effective')
            or (change.get('intake') or {}).get('tier'))


def selected_plan(evidence):
    """Only an explicitly approved selected plan can establish path expectations."""
    plan = ((evidence.get('run') or {}).get('planning') or {}).get('plan') or {}
    approval = plan.get('approval') or {}
    if not approval:
        return None
    selected = approval.get('option_id', plan.get('main_option'))
    return next((option for option in plan.get('options', []) if option.get('id') == selected), None)
