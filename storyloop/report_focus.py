"""Deterministic machine review references; never consume agent narrative text."""
import shlex

from .report_items import effective_tier, item, selected_plan


UNKNOWN_FOCUS = ('runtime_concurrency', 'expiring_constants', 'semantic_decisions',
                 'refactor_behavior_differences', 'historical_review_issues', 'performance')


def build_focus(change: dict, report: dict, evidence: dict) -> list[dict]:
    """Build machine templates from exact paths, explicit links and executed argv."""
    git = evidence.get('git') or {}
    files = git.get('files') if git.get('availability') == 'AVAILABLE' else None
    result = [item('paths', _rank_paths(change, files)), _plan_paths(evidence, files),
              item('verification', _commands(report)), item('human_verification', _human_paths(report)),
              item('layer_gaps', _layer_gaps(report))]
    if effective_tier(change, report, evidence) == 'heavy':
        result.append(item('risks', _risks(change, report, evidence)))
    result.extend(item(key, reason='NO_AUTHORITATIVE_RECORD') for key in UNKNOWN_FOCUS)
    return result


def _rank_paths(change, files):
    if files is None:
        return None
    consumers = (change.get('product') or {}).get('consumers') or []
    core = {c.get('path') for c in consumers if c.get('in_scope') is True}
    ordered = sorted(files, key=lambda f: (f['path'] not in core, not f.get('existed'), f['path']))
    return [{**{key: f.get(key) for key in ('path', 'status', 'existed', 'whitespace_only')},
             'priority': 'CORE_DECLARED_PATH' if f['path'] in core else
             'EXISTING_PATH' if f.get('existed') else 'ADDED_PATH'} for f in ordered]


def _plan_paths(evidence, files):
    plan = selected_plan(evidence)
    if plan is None or files is None:
        return item('plan_paths', reason='APPROVED_PLAN_OR_GIT_UNAVAILABLE')
    expected = set(plan.get('touched', [])) | set(plan.get('creates', []))
    actual = {f['path'] for f in files}
    return item('plan_paths', {'planned': sorted(expected), 'actual': sorted(actual),
                              'unplanned': sorted(actual - expected),
                              'not_changed': sorted(expected - actual), 'comparison': 'EXACT_PATH_SET'})


def _commands(report):
    if 'traceability' not in report:
        return None
    records = []
    for row in report['traceability']:
        for test in row.get('tests', []):
            argv = test.get('command')
            if test.get('id') != 'human' and isinstance(argv, list) and argv and all(
                    isinstance(a, str) and '\x00' not in a for a in argv):
                records.append({'criterion_id': row.get('criterion', {}).get('id'),
                                'test_id': test.get('id'), 'argv': argv, 'command': shlex.join(argv)})
    return records


def _human_paths(report):
    if 'traceability' not in report:
        return None
    return [{'criterion_id': row.get('criterion', {}).get('id'),
             'record': f'traceability/{index}/tests/{test_index}'}
            for index, row in enumerate(report['traceability'])
            for test_index, test in enumerate(row.get('tests', [])) if test.get('id') == 'human']


def _layer_gaps(report):
    if 'layers' not in report:
        return None
    tier_reasons = {'TIER_SKIPPED', 'NOT_REQUIRED_BY_TIER'}
    environment = {'NO_SYSTEM_SMOKE_SUITE', 'NOT_CONFIGURED', 'NO_COMMANDS', 'NO_TEST_COMMANDS', 'NO_REGRESSION_COMMANDS'}
    return [{'layer': name, 'kind': 'TIER_SKIP' if layer.get('reason') in tier_reasons else
             'ENVIRONMENT_GAP' if layer.get('reason') in environment else 'UNCLASSIFIED_GAP',
             'reason_code': layer.get('reason') if layer.get('reason') in tier_reasons | environment else None,
             'record': f'layers/{name}'} for name, layer in sorted(report['layers'].items())
            if layer.get('status') == 'SKIPPED']


def _risks(change, report, evidence):
    assessment = (change.get('product') or {}).get('assessment') or {}
    risks = dict(assessment.get('risks') or {})
    run = evidence.get('run') or {}
    routing = run.get('routing') or {}
    plan = selected_plan(evidence) or {}
    for key, value in (plan.get('risks') or {}).items():
        prior = risks.get(key) or {}
        risks[key] = {**value, 'value': 'yes' if prior.get('value') == 'yes' else value.get('value')}
    for key, value in (routing.get('assessed_risks') or {}).items():
        if value == 'yes' or key not in risks:
            risks[key] = {**risks.get(key, {}), 'value': value}
    for update in routing.get('risk_updates') or []:
        risks[update['risk']] = {'value': 'yes'}
    if not risks:
        return None
    return [_risk_row(key, value, report) for key, value in sorted(risks.items())]


def _risk_row(key, value, report):
    value = value if isinstance(value, dict) else {'value': value}
    criteria = set(value.get('criteria') or [])
    linked = [row for row in report.get('traceability', []) if row.get('criterion', {}).get('id') in criteria]
    tests = sorted({test['id'] for row in linked for test in row.get('tests', [])
                    if test.get('id') != 'human' and test.get('result', {}).get('status') in {'PASS', 'PASSED'}})
    return {'risk': key, 'declared_value': value.get('value'),
            'coverage': 'EXPLICIT_CRITERION_LINK' if tests else 'UNCOVERED',
            'criteria': sorted(criteria), 'tests': tests, 'provenance': 'PERSISTED_RISK_DECLARATION'}
