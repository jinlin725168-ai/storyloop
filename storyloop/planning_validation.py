"""Structured role-owned plan fields and deterministic scope validation."""
from copy import deepcopy
from pathlib import Path

from .assessment import _evidence
from .baseline import load_baseline, load_project_config
from .intake import digest, _validate_locations
from .models import require, text
from .principles import check_principles, load_principles
from .path_security import repository_relative, safe_target
from .project_boundaries import _category, _inside
from .role_schemas import schema_errors, schema_for
from .routing_evidence import measure_scope
from .smoke import smoke_digests

SCOPE_KEYS = ('touched', 'creates', 'interfaces', 'symbols')


def option_scope(option: dict) -> dict:
    """Return the full measurable scope, including symbol-to-file associations."""
    return {key: deepcopy(option[key]) for key in SCOPE_KEYS}


def validate_plan(answer: object, project: Path, change: dict, state: dict) -> dict:
    """Normalize role output; questions return NEEDS_INPUT without inventing a plan."""
    errors = schema_errors(answer, schema_for('design', {'mode': 'routing-plan'}))
    require(not errors, 'Invalid routing plan: ' + '; '.join(errors))
    if answer['questions']:
        for question in answer['questions']:
            text(question, 'planning question')
        return {'status': 'NEEDS_INPUT', 'questions': list(answer['questions'])}
    require(1 <= len(answer['options']) <= 3, 'Plan needs one to three options')
    text(answer['design'], 'plan design')
    options = [_option(item, project, change, state) for item in answer['options']]
    ids = [item['id'] for item in options]
    require(len(ids) == len(set(ids)), 'Duplicate option id')
    require(answer['main_option'] in ids, 'Main option is absent')
    binding = {'product_digest': digest(change['product']),
               'source_commit': state['contract_commit'],
               'scope_digest': digest([{ 'id': o['id'], **option_scope(o)} for o in options]),
               'suite_digest': digest(smoke_digests(project, change['change_id'])),
               'policy_digest': state['routing']['policy_digest'],
               'principles_digest': load_principles(project)['digest'],
               'baseline_digest': digest(load_baseline(project))}
    plan = {'version': 1, 'binding': binding, 'main_option': answer['main_option'],
            'options': options, 'questions': [], 'design': answer['design'].strip() + '\n'}
    return {**plan, 'id': digest(plan), 'status': 'DONE'}


def _option(item: dict, project: Path, change: dict, state: dict) -> dict:
    option = deepcopy(item)
    text(option['id'], 'option id')
    text(option['summary'], 'option summary')
    measured = measure_scope(project, state['contract_commit'], option_scope(option),
                             state['routing']['policy'])
    option.update(measured['scope'])
    _paths_and_constraints(project, change, option)
    require(bool(option['regression']), 'Regression steps are required')
    for step in option['regression'] + option['migration']['steps']:
        text(step, 'verification or migration step')
    require(bool(option['migration']['steps']) or bool(option['migration']['reason'].strip()),
            'Migration needs steps or an explicit no-migration reason')
    for risk in option['risks'].values():
        _evidence(risk['evidence'], risk['value'] != 'unknown')
    _validate_locations(project, state['contract_commit'], {'risks': option['risks'], 'criteria': []})
    _structured_checks(option)
    check_principles(option, load_principles(project))
    return option


def _paths_and_constraints(project: Path, change: dict, option: dict) -> None:
    baseline = load_baseline(project)
    roots = load_project_config(project)['profile'].get('test_roots', [])
    paths = option['touched'] + option['creates']
    forbidden = [repository_relative(p, 'forbidden path') for p in option['forbidden_paths']]
    option['forbidden_paths'] = sorted(set(forbidden))
    for path in paths:
        safe_target(project, path, 'plan path')
        require(not any(_inside(path, root) for root in forbidden), 'Forbidden path overlaps plan scope')
        require(_category(path, baseline, change['change_id'], roots) in {'FEATURE', 'TEST', 'REPORT'},
                f'Plan path outside approved write boundary: {path}')
        # Reject a regular file used as a parent, including a parent being created by this option.
        require(not any(_inside(path, other) and path != other for other in paths),
                'Plan scope contains conflicting file and descendant paths')
        require(not any(parent.is_file() for parent in (project / path).parents
                        if parent != project and parent.is_relative_to(project)),
                'Created path has a non-directory parent')
    criteria = {c['id'] for story in change['product']['stories'] for c in story['acceptance']}
    for constraint in option['constraints']:
        require(constraint['criterion_id'] in criteria, 'Constraint references unknown criterion')
        constraint['path'] = repository_relative(constraint['path'], 'constraint path')
        require(constraint['path'] in paths, 'Constraint path is outside option scope')
        text(constraint['rule'], 'constraint rule')


def _structured_checks(option: dict) -> None:
    for dependency in option['dependencies']:
        for key in ('ecosystem', 'name', 'version'):
            text(dependency[key], f'dependency {key}')
        require(dependency['verification']['check_index'] >= 0, 'Invalid dependency check_index')
    seen = set()
    for check in option['principle_checks']:
        text(check['principle_id'], 'principle id')
        require(check['principle_id'] not in seen, 'Duplicate principle check')
        seen.add(check['principle_id'])
        text(check['reason'], 'principle check reason')
