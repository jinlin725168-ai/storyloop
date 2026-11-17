"""Read-only L1 intake evaluation and locked approval binding verification."""
from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from .change_types import definition_for, type_of
from .errors import require_role_evidence, role_evidence_error
from .assessment import validate_assessment
from .baseline import _git, load_project_config
from .models import StoryLoopError, require
from .state_format import RED_ROWS
from .product_contract import validate_product
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
            'contract_change': contract_change(product),
            'risks': risks}


def contract_change(product: dict) -> str:
    """Classify the contract as safe (additive), breaking, or review from rows and existence.

    safe: every changed-behaviour row covers behaviour that does not exist yet, and at least one
    must-hold row pins behaviour that already exists. breaking: a changed-behaviour row covers
    existing behaviour. Anything else, including contracts without row types, needs review.
    """
    existence = {item['criterion_id']: item['existence']
                 for item in (product.get('assessment') or {}).get('criteria') or []}
    rows = [criterion for story in product['stories'] for criterion in story['acceptance']
            if criterion.get('kind') == 'automated']
    if not rows or any('row_type' not in row for row in rows):
        return 'review'
    changed = [existence.get(row['id']) for row in rows if row['row_type'] in RED_ROWS]
    if any(state in ('exists', 'partial') for state in changed):
        return 'breaking'
    pinned = [row for row in rows if row['row_type'] not in RED_ROWS
              and existence.get(row['id']) == 'exists']
    return 'safe' if changed and all(state == 'absent' for state in changed) and pinned else 'review'


def _status(project: Path, commit: str, product: dict, decision: dict | None) -> str:
    assessment = product['assessment']
    if exploration_required(assessment['verifiable_goal'], len(product['questions']),
                            len(product['stories'])):
        return 'EXPLORATION_RECOMMENDED'
    criteria = assessment['criteria']
    if any(item['feasibility'] == 'infeasible' or _unavailable(project, commit, item) for item in criteria):
        return 'ORACLE_INFEASIBLE'
    return 'NEEDS_INPUT' if needs_input_reasons(product, decision) else 'READY'


def needs_input_reasons(product: dict, decision: dict | None) -> list[str]:
    """Name each rule that keeps a draft at NEEDS_INPUT, so a stop can say what to revise."""
    assessment = product.get('assessment')
    if not assessment or not decision:
        return ['缺少产品评估或分流决定，请补充评估后重新起草']
    criteria = assessment['criteria']
    reasons = []
    if product['questions']:
        reasons.append(f"仍有 {len(product['questions'])} 个待决问题")
    risks = [field for field in decision['unresolved'] if field.startswith('risks.')]
    if risks:
        reasons.append('未决风险：' + ', '.join(risks))
    if _manual_only_automation(product):
        reasons.append('自动验收条件的可行性被评为 manual')
    unknown = [item['criterion_id'] for item in criteria
               if item['feasibility'] == 'unknown' or item['existence'] == 'unknown']
    if unknown:
        reasons.append('验收条件的可行性或现状为 unknown：' + ', '.join(unknown))
    if definition_for(type_of(product)).requires_new_criteria:
        partial = [item['criterion_id'] for item in criteria if item['existence'] == 'partial']
        if partial:
            reasons.append(f"{type_of(product)} 类变更要求验收新行为，但 existence 为 partial："
                           + ', '.join(partial) + '（被验收的行为若尚不存在应为 absent）')
        elif all(item['existence'] == 'exists' for item in criteria):
            reasons.append(f"{type_of(product)} 类变更要求验收新行为，但全部验收条件 existence 为 exists")
    return reasons


def _manual_only_automation(product: dict) -> bool:
    kinds = {criterion['id']: criterion['kind'] for story in product['stories']
             for criterion in story['acceptance']}
    return any(kinds[item['criterion_id']] == 'automated' and item['feasibility'] == 'manual'
               for item in product['assessment']['criteria'])


def infeasible_reasons(project: Path, commit: str, product: dict) -> list[str]:
    """Name each criterion that makes intake ORACLE_INFEASIBLE, so the stop says what to fix."""
    reasons = []
    for item in product['assessment']['criteria']:
        if item['feasibility'] == 'infeasible':
            reasons.append(f"{item['criterion_id']}: 判据标为 infeasible")
        elif item['feasibility'] == 'local_automated':
            missing = sorted({command[0] for command in item['method']['commands']
                              if not _executable_available(project, commit, command[0])})
            reasons += [f"{item['criterion_id']}: 找不到可执行程序 {name}" for name in missing]
    return reasons


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
        # The engine's own interpreter directory counts too: project tools installed in the
        # same venv (e.g. a console script) run there without the venv being activated.
        search = os.pathsep.join([str(Path(sys.executable).parent),
                                  *(p for p in os.get_exec_path() if Path(p).is_absolute())])
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
            require_role_evidence(match is not None, 'Evidence location must be path:line')
            try:
                path = repository_relative(match[1], 'evidence path')
            except StoryLoopError as exc:
                raise role_evidence_error(exc) from exc
            if path not in lines:
                content = _evidence_content(project, commit, path, verify_commit=not lines)
                lines[path] = len(content.splitlines())
            require_role_evidence(int(match[2]) <= lines[path], 'Evidence line absent at source commit')


def _evidence_content(project: Path, commit: str, path: str, verify_commit: bool = True) -> str:
    """Read evidence only from a valid source commit; a missing path is role-repairable."""
    if verify_commit:
        _git(project, 'cat-file', '-e', f'{commit}^{{commit}}')
    visible = _git(project, 'ls-tree', '-z', '--name-only', commit, '--', path).split('\0')
    require_role_evidence(path in visible, f'Evidence path absent at source commit {commit}: {path}')
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
        details = {'intake': current}
        if current['status'] == 'NEEDS_INPUT':
            details['reasons'] = needs_input_reasons(change['product'], current['decision'])
        elif current['status'] == 'ORACLE_INFEASIBLE':
            details['reasons'] = infeasible_reasons(project, current['source_commit'], change['product'])
        raise StoryLoopError(current['status'], code=current['status'], details=details)
    previous = change.get('intake')
    if previous != current:
        # 2026-11-17 L3 #474: the stop read only "NEEDS_INPUT" with no reason or way forward.
        raise StoryLoopError('NEEDS_INPUT: intake evidence is stale; revise the draft',
                             code='NEEDS_INPUT', details={'intake': current, 'reasons': [
                                 '草案的分流证据已过期（代码、环境或判定规则变化后重新评估的结果不同），'
                                 '用 storyloop change --message 刷新草案后再批准']})
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
