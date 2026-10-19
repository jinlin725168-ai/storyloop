"""Structural validation of product-reported intake evidence (never executes commands)."""
from .models import fields, require, strings, text
from .path_security import repository_relative
from .routing_policy import RISK_FIELDS


def validate_assessment(value: object, product: dict) -> None:
    """Require one correctly owned judgment for every acceptance criterion."""
    fields(value, {'verifiable_goal', 'scope', 'risks', 'criteria'})
    require(type(value['verifiable_goal']) is bool, 'Invalid verifiable_goal')
    fields(value['scope'], {'touched', 'creates', 'interfaces', 'symbols'})
    for key in ('touched', 'creates', 'interfaces'):
        strings(value['scope'][key], key)
    require(isinstance(value['scope']['symbols'], list), 'Invalid scope symbols')
    for symbol in value['scope']['symbols']:
        fields(symbol, {'path', 'symbol'})
        text(symbol['path'], 'symbol path')
        text(symbol['symbol'], 'symbol')
    fields(value['risks'], set(RISK_FIELDS))
    for risk in value['risks'].values():
        fields(risk, {'value', 'evidence'})
        require(risk['value'] in ('yes', 'no', 'unknown'), 'Invalid risk value')
        _evidence(risk['evidence'], risk['value'] != 'unknown')
    expected = {(s['id'], c['id']) for s in product['stories'] for c in s['acceptance']}
    require(isinstance(value['criteria'], list), 'Invalid assessment criteria')
    seen = set()
    for item in value['criteria']:
        _criterion(item)
        key = (item['story_id'], item['criterion_id'])
        require(key in expected and key not in seen, 'Unknown or duplicate assessment criterion')
        seen.add(key)
    require(seen == expected, 'Missing assessment criterion')


def _criterion(item: object) -> None:
    fields(item, {'story_id', 'criterion_id', 'feasibility', 'method', 'existence', 'evidence'})
    text(item['story_id'], 'story id')
    text(item['criterion_id'], 'criterion id')
    require(item['feasibility'] in ('local_automated', 'manual', 'infeasible', 'unknown'),
            'Invalid criterion feasibility')
    require(item['existence'] in ('exists', 'partial', 'absent', 'unknown'), 'Invalid existence')
    _evidence(item['evidence'], item['feasibility'] in ('local_automated', 'manual')
              or item['existence'] != 'unknown')
    method = item['method']
    fields(method, {'commands', 'steps', 'expected'})
    require(isinstance(method['commands'], list), 'Invalid method commands')
    for command in method['commands']:
        require(bool(strings(command, 'method command')), 'Empty method command')
    strings(method['steps'], 'method steps')
    text(method['expected'], 'method expected result or infeasibility reason')
    if item['feasibility'] == 'local_automated':
        require(bool(method['commands']), 'Automated method needs commands')
    if item['feasibility'] == 'manual':
        require(bool(method['steps']), 'Manual method needs executable steps')


def _evidence(value: object, known: bool) -> None:
    fields(value, {'locations', 'search_scope'})
    strings(value['locations'], 'evidence locations')
    strings(value['search_scope'], 'evidence search scope')
    require(not known or bool(value['locations'] or value['search_scope']),
            'Known judgment requires evidence')
    for scope in value['search_scope']:
        repository_relative(scope, 'search scope')
