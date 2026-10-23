"""Read-only L1 intake evaluation and locked approval binding verification."""
from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from .change_types import definition_for, type_of
from .assessment import validate_assessment
from .baseline import _git, load_project_config
from .models import StoryLoopError, require, validate_product
from .path_security import repository_relative, safe_target
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
    result['status'] = _status(project, source_commit, product, result['decision'])
    return result


def _signals(product: dict, measured: dict) -> dict:
    assessment = product['assessment']
    risks = {key: item['value'] for key, item in assessment['risks'].items()}
    rank = {'no': 0, 'unknown': 1, 'yes': 2}
    for key, value in measured['signals']['risks'].items():
        risks[key] = max(risks[key], value, key=rank.get)
    return {**measured['signals'], 'nature': type_of(product),
            'stories': len(product['stories']), 'criteria': len(assessment['criteria']),
            # Feasibility describes available supporting methods, never changes contract kind.
            'manual': sum(c['kind'] == 'manual' for story in product['stories']
                          for c in story['acceptance']),
            'risks': risks}


def _status(project: Path, commit: str, product: dict, decision: dict | None) -> str:
    assessment = product['assessment']
    if exploration_required(assessment['verifiable_goal'], len(product['questions']),
                            len(product['stories'])):
        return 'EXPLORATION_RECOMMENDED'
    criteria = assessment['criteria']
    if any(item['feasibility'] == 'infeasible' or _unavailable(project, commit, item) for item in criteria):
        return 'ORACLE_INFEASIBLE'
    if (product['questions'] or not decision
            or any(field.startswith('risks.') for field in decision['unresolved'])
            or _manual_only_automation(product)
            or any(item['feasibility'] == 'unknown' or item['existence'] == 'unknown'
                   for item in criteria)):
        return 'NEEDS_INPUT'
    if (definition_for(type_of(product)).requires_new_criteria
            and (all(item['existence'] == 'exists' for item in criteria)
                 or any(item['existence'] == 'partial' for item in criteria))):
        return 'NEEDS_INPUT'
    return 'READY'


def _manual_only_automation(product: dict) -> bool:
    kinds = {criterion['id']: criterion['kind'] for story in product['stories']
             for criterion in story['acceptance']}
    return any(kinds[item['criterion_id']] == 'automated' and item['feasibility'] == 'manual'
               for item in product['assessment']['criteria'])


def _unavailable(project: Path, commit: str, item: dict) -> bool:
    if item['feasibility'] != 'local_automated':
        return False
    return any(not _executable_available(project, commit, command[0])
               for command in item['method']['commands'])


def _executable_available(project: Path, commit: str, executable: str) -> bool:
    if executable == '{python}':
        return True
    if '/' not in executable and "\\" not in executable:
        # Relative PATH entries would accidentally bind availability to controller cwd.
        search = os.pathsep.join(p for p in os.get_exec_path() if Path(p).is_absolute())
        return shutil.which(executable, path=search) is not None
    candidate = Path(executable)
    if candidate.is_absolute():
        if not candidate.is_relative_to(project):
            return shutil.which(executable) is not None
        executable = candidate.relative_to(project).as_posix()
    try:
        relative = executable[2:] if executable.startswith('./') else executable
        relative = repository_relative(relative, 'method executable')
        safe_target(project, relative, 'method executable')
        entry = _git(project, 'ls-tree', '-z', commit, '--', relative)
        records = [row for row in entry.split('\0') if row]
        return len(records) == 1 and records[0].startswith('100755 blob ') and (
            records[0].split('\t', 1)[1] == relative)
    except (StoryLoopError, OSError):
        return False


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
    # All locations share one commit: verify it once and read each path once (3 git
    # processes per location made intake the slowest gate of every change/approve/run).
    lines: dict[str, int] = {}
    for proof in proofs:
        for location in proof['locations']:
            match = re.fullmatch(r'(.+):([1-9][0-9]*)', location)
            require(match is not None, 'Evidence location must be path:line')
            path = repository_relative(match[1], 'evidence path')
            if path not in lines:
                content = _evidence_content(project, commit, path, verify_commit=not lines)
                lines[path] = len(content.splitlines())
            require(int(match[2]) <= lines[path], 'Evidence line absent at source commit')


def _evidence_content(project: Path, commit: str, path: str, verify_commit: bool = True) -> str:
    """Read evidence only from a valid source commit; a missing path is role-repairable."""
    if verify_commit:
        _git(project, 'cat-file', '-e', f'{commit}^{{commit}}')
    visible = _git(project, 'ls-tree', '-z', '--name-only', commit, '--', path).split('\0')
    require(path in visible, f'Evidence path absent at source commit {commit}: {path}')
    return _git(project, 'show', f'{commit}:{path}')


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
