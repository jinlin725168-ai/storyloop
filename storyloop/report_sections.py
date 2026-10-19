"""Reader sections with explicit human, agent and engine provenance."""
from .report_items import item


def header(change, report, evidence):
    """Header identity and lifecycle commit availability."""
    product = change.get('product') or {}
    routing = (evidence.get('run') or {}).get('routing') or {}
    view = report.get('routing') or {}
    result = [item('identity', {'change_id': change.get('change_id'), 'nature': product.get('nature'),
                              'subtype': (product.get('refactor') or {}).get('subtype'), 'status': report.get('status')}),
              item('tier', {'initial': routing.get('initial_tier', view.get('initial')),
                            'effective': routing.get('effective_tier', view.get('effective')),
                            'reasons': routing.get('reasons', view.get('rules'))})]
    result.extend(item(name, report.get(name), availability='AVAILABLE' if report.get(name) else
                       'PENDING' if name in {'merge_commit', 'report_commit'} else 'NOT_AVAILABLE')
                  for name in ('implementation_commit', 'report_commit', 'merge_commit'))
    return result


def conclusion(change, report, reconciliation):
    """Keep approved requirements and human confirmations separate from engine counts."""
    product = change.get('product') or {}
    trace = report.get('traceability')
    criteria = [c for s in product.get('stories', []) for c in s.get('acceptance', [])]
    complete = {row.get('criterion', {}).get('id') for row in trace or [] if row.get('tests') and
                all(t.get('result', {}).get('status') in {'PASS', 'PASSED', 'ACCEPTED'} for t in row['tests'])}
    manual = [dict(test, criterion_id=row.get('criterion', {}).get('id')) for row in trace or []
              for test in row.get('tests', []) if test.get('id') == 'human']
    result = [item('goal', product.get('goal'), 'human' if change.get('approval') else 'agent_sample',
                   'APPROVED_CONTRACT' if change.get('approval') else 'PRODUCT_DECLARATION'),
              item('non_goals', product.get('non_goals'), 'human' if change.get('approval') else 'agent_sample'),
              item('criteria', criteria if 'stories' in product else None, 'agent_sample'),
              item('ac_matrix', _ac_matrix(trace)), item('consumer_matrix', _consumers(product, trace)),
              item('gates', _gates(report)), item('completion',
                  {'completed': len(complete & {c['id'] for c in criteria}), 'total': len(criteria)}
                  if 'stories' in product and trace is not None else None),
              item('manual_confirmations', manual if trace is not None else None, 'human'),
              item('reconciliation', reconciliation)]
    result.extend(item(key, reason='NO_AUTHORITATIVE_COMPARABLE_COUNT') for key in ('discovered', 'added'))
    return result


def _ac_matrix(trace):
    if trace is None:
        return None
    return [{'criterion_id': row.get('criterion', {}).get('id'),
             'tests': [{'id': t.get('id'), 'status': t.get('result', {}).get('status')} for t in row.get('tests', [])],
             'evidence_sha256': row.get('evidence_sha256')} for row in trace]


def _consumers(product, trace):
    if 'consumers' not in product:
        return None
    return [{'consumer_id': c.get('id'), 'path': c.get('path'), 'in_scope': c.get('in_scope'),
             'criteria': [r.get('criterion', {}).get('id') for r in trace or []
                          if c.get('id') in r.get('criterion', {}).get('consumers', [])] if trace is not None else None}
            for c in product['consumers']]


def _gates(report):
    if 'layers' not in report:
        return None
    layers = report['layers']
    return {'layers': {key: value.get('status') for key, value in layers.items()},
            'passed': sum(v.get('status') == 'PASSED' for v in layers.values()),
            'skipped': sum(v.get('status') == 'SKIPPED' for v in layers.values()), 'total': len(layers),
            'review_verdict': (report.get('review') or {}).get('verdict')}


def proof(report, evidence):
    """Expose call-bound skills, protected hashes and explicitly unavailable external CI."""
    oracle = report.get('oracle') or {}
    ledger = (evidence.get('entries') or {}).get('ledger')
    ledger_available = (evidence.get('availability') or {}).get('entries') != 'NOT_AVAILABLE'
    skills = [{'call': entry.get('call'), 'role': entry.get('role'), 'skills': entry.get('skills'),
               'availability': 'AVAILABLE' if 'skills' in entry else 'NOT_AVAILABLE'} for entry in ledger or []]
    result = [item('traceability', _ac_matrix(report.get('traceability'))),
              item('layers', report.get('layers')), item('evidence_hashes',
                  (evidence.get('availability') or {}).get('evidence')),
              item('skills', skills if ledger is not None and ledger_available else None),
              item('skills_legacy', oracle.get('skills_used'), reason='STALE_POSSIBLE'),
              item('ci', reason='NO_AUTHORITATIVE_EXTERNAL_CI_RECORD')]
    result.extend(item(key, oracle.get(key)) for key in ('baseline', 'executed', 'uncounted'))
    return result


def process(change, report, evidence):
    """Preserve agent claims and human decisions without upgrading their authority."""
    run = evidence.get('run') or {}
    result = [item(key, run.get(key)) for key in ('failures', 'agent_failures', 'repairs')]
    result.extend(item(key, run.get(key), 'human') for key in ('approvals', 'overrules', 'resets', 'reset'))
    result.extend([item('answers', evidence.get('answers'), 'human',
                        'SNAPSHOT_TRUNCATED' if (evidence.get('availability') or {}).get('answers_truncated') else None),
                   item('product_approval', change.get('approval'), 'human'),
                   item('review', report.get('review'), 'agent_sample'),
                   item('escalations', (run.get('routing') or {}).get('history')),
                   item('chronology', reason='ONLY_PERSISTED_RECORDS_NO_COMPLETE_EVENT_HISTORY'),
                   item('fact_history', reason='FACT_EVOLUTION_NOT_PERSISTED')])
    facts = run.get('facts')
    if facts is None:
        result.append(item('facts'))
    elif not facts:
        result.append(item('facts', [], 'agent_code'))
    else:
        result.extend(item(f'facts:{index}', fact, 'agent_code' if fact.get('location') else 'agent_sample',
                           'AGENT_CLAIM_LOCATION_ONLY_VALIDATED' if fact.get('location') else 'AGENT_CLAIM')
                      for index, fact in enumerate(facts))
    planning = run.get('planning') or {}
    result.extend(item(key, planning.get(key), 'human') for key in ('continue_heavy',))
    result.append(item('design', (planning.get('plan') or {}).get('design'), 'agent_sample'))
    result.append(item('design_approval', (planning.get('plan') or {}).get('approval'), 'human'))
    return result
