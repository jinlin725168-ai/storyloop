"""Read-only L1 intake evaluation and locked approval binding verification."""
from __future__ import annotations
import hashlib
import json
import re
import shutil
from pathlib import Path
from .assessment import validate_assessment
from .baseline import _git, load_project_config
from .models import StoryLoopError, require, validate_product
from .path_security import repository_relative
from .readiness import _require_readiness_locked
from .routing_evidence import measure_scope, _repository_and_commit
from .routing_policy import evaluate, validate_policy


def digest(value: object) -> str:
    """Canonical UTF-8 fingerprint shared by intake bindings."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                  ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def exploration_required(verifiable_goal: bool, questions: int, stories: int) -> bool:
    """Recommend exploration when no observable goal or questions dominate stories."""
    return not verifiable_goal or (questions >= 2 and questions / (questions + stories) >= .5)


def assess_intake(project: Path, product: dict, source_commit: str, policy: dict) -> dict:
    """Evaluate product and commit evidence without writing or running proposed methods."""
    validate_product(product)
    policy = validate_policy(policy)
    project, source_commit = _repository_and_commit(project, source_commit)
    assessment = product.get('assessment')
    result = {'version': 1, 'product_digest': digest(product), 'source_commit': source_commit,
              'policy_digest': digest(policy), 'assessment_digest': digest(None),
              'decision': None, 'status': 'NEEDS_INPUT', 'evidence': []}
    if assessment is None:
        return result
    validate_assessment(assessment, product)
    _validate_locations(project, source_commit, assessment)
    measured = measure_scope(project, source_commit, assessment['scope'], policy)
    evidence = measured['evidence'] + _role_evidence(assessment, source_commit)
    result['evidence'] = evidence
    result['assessment_digest'] = digest({'assessment': assessment, 'evidence': evidence})
    if assessment['criteria']:
        signals = _signals(product, measured)
        result['decision'] = evaluate(signals, policy)
    result['status'] = _status(product, result['decision'])
    return result


def _signals(product: dict, measured: dict) -> dict:
    assessment = product['assessment']
    risks = {key: item['value'] for key, item in assessment['risks'].items()}
    rank = {'no': 0, 'unknown': 1, 'yes': 2}
    for key, value in measured['signals']['risks'].items():
        risks[key] = max(risks[key], value, key=rank.get)
    return {**measured['signals'], 'nature': product.get('nature', 'feature'),
            'stories': len(product['stories']), 'criteria': len(assessment['criteria']),
            'manual': sum(item['feasibility'] == 'manual' for item in assessment['criteria']),
            'risks': risks}


def _status(product: dict, decision: dict | None) -> str:
    assessment = product['assessment']
    if exploration_required(assessment['verifiable_goal'], len(product['questions']),
                            len(product['stories'])):
        return 'EXPLORATION_RECOMMENDED'
    criteria = assessment['criteria']
    if any(item['feasibility'] == 'infeasible' or _unavailable(item) for item in criteria):
        return 'ORACLE_INFEASIBLE'
    if (product['questions'] or not decision or decision['unresolved']
            or any(item['feasibility'] == 'unknown' or item['existence'] == 'unknown'
                   for item in criteria)):
        return 'NEEDS_INPUT'
    if (product.get('nature', 'feature') == 'feature'
            and (all(item['existence'] == 'exists' for item in criteria)
                 or any(item['existence'] == 'partial' for item in criteria))):
        return 'NEEDS_INPUT'
    return 'READY'


def _unavailable(item: dict) -> bool:
    if item['feasibility'] != 'local_automated':
        return False
    return any(command[0] != '{python}' and shutil.which(command[0]) is None
               for command in item['method']['commands'])


def _role_evidence(assessment: dict, commit: str) -> list[dict]:
    judgments = [(f'risks.{key}', item['value'], item['evidence'])
                 for key, item in assessment['risks'].items()]
    judgments.extend((f"criteria.{item['criterion_id']}", item, item['evidence'])
                     for item in assessment['criteria'])
    return [{'field': field, 'value': value, 'source': 'role',
             'locations': proof['locations'], 'search_scope': proof['search_scope'], 'commit': commit}
            for field, value, proof in judgments]


def _validate_locations(project: Path, commit: str, assessment: dict) -> None:
    proofs = [item['evidence'] for item in assessment['risks'].values()]
    proofs.extend(item['evidence'] for item in assessment['criteria'])
    for proof in proofs:
        for location in proof['locations']:
            match = re.fullmatch(r'(.+):([1-9][0-9]*)', location)
            require(match is not None, 'Evidence location must be path:line')
            path = repository_relative(match[1], 'evidence path')
            content = _git(project, 'show', f'{commit}:{path}')
            require(int(match[2]) <= len(content.splitlines()), 'Evidence line absent at source commit')


def project_intake(project: Path, product: dict) -> dict:
    """Project a draft against current committed source and trusted routing rules."""
    policy = validate_policy(load_project_config(project).get('routing'))
    return assess_intake(project, product, _git(project, 'rev-parse', 'HEAD').strip(), policy)


def require_intake(project: Path, change: dict) -> dict:
    """Recheck readiness and all intake bindings; caller must own the project lock."""
    _require_readiness_locked(project)
    current = project_intake(project, change['product'])
    if current['status'] != 'READY':
        raise StoryLoopError(current['status'], code=current['status'], details={'intake': current})
    previous = change.get('intake')
    if previous != current:
        raise StoryLoopError('NEEDS_INPUT: intake evidence is stale; revise the draft',
                             code='NEEDS_INPUT', details={'intake': current})
    approval = change.get('approval')
    if approval:
        require(approval.get('intake_digest') == digest(current), 'Approved intake is stale')
    return current


def validate_intake_record(value: object) -> None:
    """Reject malformed persisted intake without recalculating legacy contracts."""
    from .models import fields
    fields(value, {'version', 'product_digest', 'source_commit', 'policy_digest',
                   'assessment_digest', 'decision', 'status', 'evidence'})
    require(type(value['version']) is int and value['version'] == 1, 'Invalid intake version')
    for key in ('product_digest', 'policy_digest', 'assessment_digest', 'source_commit'):
        require(isinstance(value[key], str) and re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', value[key])
                is not None, 'Invalid intake binding')
    require(value['status'] in ('READY', 'NEEDS_INPUT', 'EXPLORATION_RECOMMENDED',
                                'ORACLE_INFEASIBLE'), 'Invalid intake status')
    require(isinstance(value['evidence'], list), 'Invalid intake evidence')
    if value['decision'] is not None:
        decision = value['decision']
        fields(decision, {'tier', 'split', 'reasons', 'unresolved'})
        require(decision['tier'] in ('light', 'standard', 'heavy'), 'Invalid intake tier')
        require(type(decision['split']) is bool, 'Invalid intake split')
        require(isinstance(decision['reasons'], list) and isinstance(decision['unresolved'], list),
                'Invalid intake reasons')
