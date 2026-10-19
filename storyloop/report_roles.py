"""Append-only role diagnostics; adapter claims never become verification evidence."""
from .report_items import item


NEW_FIELDS = {'tier_requested', 'adapter', 'degraded', 'path_guard', 'subagents_applied'}


def calls(evidence):
    """Return bounded call records only when role-profile diagnostics exist."""
    rows = (evidence.get('entries') or {}).get('ledger') or []
    if (evidence.get('availability') or {}).get('entries') == 'NOT_AVAILABLE':
        return []
    return rows if any(NEW_FIELDS.intersection(row) for row in rows) else []


def cost_items(evidence):
    """Distinguish absent configuration from missing/mismatched adapter reports."""
    rows = calls(evidence)
    if not rows:
        return []
    result = []
    for row in rows:
        requested, applied = row.get('tier_requested'), row.get('tier_applied')
        configured = requested in ('strong', 'standard')
        matched = configured and requested == applied
        result.append({**{key: row.get(key) for key in ('call', 'role', 'adapter', 'model', 'vendor')},
                       'tier_requested': requested, 'tier_applied': applied,
                       'status': '已生效' if matched else '未生效' if configured else '未配置',
                       'statistics_eligible': matched})
    items = [item('role_calls', result, reason='ADAPTER_REPORTED_NOT_VERIFIED')]
    if evidence.get('role_downgrades'):
        items.append(item('role_downgrades', evidence['role_downgrades'], 'human',
                          reason='CONFIGURED_REASON_NOT_NEW_APPROVAL'))
    return items


def protection_items(evidence):
    """Render reported guard and subagent modes with explicit unknown states."""
    rows = calls(evidence)
    if not rows:
        return []
    return [item('role_protection', [_protection(row) for row in rows],
                 reason='ADAPTER_REPORTED_NOT_VERIFIED')]


def _protection(row):
    mode = row.get('subagents_applied')
    mode = mode.get('mode') if isinstance(mode, dict) else None
    guard = row.get('path_guard')
    return {'call': row.get('call'), 'role': row.get('role'),
            'path_guard': '生效' if guard == 'enforced' else '不支持' if guard == 'unsupported' else '未生效',
            'subagents': '并行' if mode == 'parallel' else '依次' if mode == 'sequential' else '未生效',
            'reported': row.get('subagents_applied')}


def finding_refs(findings):
    """Allowlist references; never copy message, trigger, impact, fix or agent IDs."""
    return [{key: row.get(key) for key in ('id', 'location', 'severity', 'confidence')}
            for row in findings]


def conclusion_items(evidence):
    """Report actual vendor comparison and unresolved severe low-confidence references."""
    result = []
    disposition = (evidence.get('run') or {}).get('review_disposition')
    if disposition is not None:
        result.append(item('review_human', finding_refs(disposition.get('human') or [])))
    rows = calls(evidence)
    if not rows:
        return result
    comparisons, implement = [], None
    for row in rows:
        if row.get('role') == 'implement':
            implement = row
        if row.get('role') != 'review':
            continue
        first, second = (implement or {}).get('vendor'), row.get('vendor')
        distinct = (isinstance(first, str) and isinstance(second, str)
                    and bool(first.strip() and second.strip()) and first.casefold() != second.casefold())
        status = ('本次审查与实现同一厂商' if row.get('degraded') == 'cross_vendor' else
                  '已生效' if distinct else '跨厂商未生效')
        comparisons.append({'call': row.get('call'), 'implement_vendor': first,
                            'review_vendor': second, 'status': status})
    if comparisons:
        result.append(item('cross_vendor', comparisons, reason='ADAPTER_REPORTED_NOT_VERIFIED'))
    return result
