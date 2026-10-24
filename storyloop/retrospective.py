"""Read-only post-run learning; telemetry is diagnostic evidence, never authority."""
from collections import Counter
from datetime import date
from pathlib import Path
import re

from .baseline import _git, load_project_config
from .changes import load_change
from .cost_limits import check_before_call
from .drafts import create_draft
from .facts import valid_location
from .failure_attribution import _review_identity
from .lesson_store import load_lessons_at, stale_ids, effective_status
from .lessons import ACTIONS, LANDING_KINDS, parse_lesson, render_lesson
from .models import StoryLoopError, identifier, require
from .path_security import safe_target, repository_relative
from .project_agents import invoke_project_agent, ROLES
from .guarded_calls import read_only_call
from .project_loop_support import save_run
from .stop_report import STOP_STATUSES
from .call_log import call_folder
from .storage import load_json, locked, write_json
from .telemetry import ledger_entries


def _meta_path(project, call):
    require(isinstance(call, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', call),
            '调用证据 call_id 无效')
    return safe_target(project, (call_folder(project, call) / 'meta.json').relative_to(project).as_posix(), 'call evidence')


def _provided(project, change_id):
    rows = []
    for row in ledger_entries(project, identifier(change_id, 'change id')):
        require(isinstance(row, dict), '经验调用证据不是对象')
        evidence = row
        if row.get('lessons_truncated'):
            path = _meta_path(project, row.get('call'))
            require(path.is_file(), '截断账本的完整经验调用证据缺失')
            evidence = load_json(path)
            for field in ('lessons_provided', 'lessons_claimed', 'lessons_unknown'):
                require(isinstance(evidence.get(field), list)
                        and all(isinstance(x, str) for x in evidence[field]), '完整经验调用证据损坏：' + field)
        ids = evidence.get('lessons_provided', [])
        require(isinstance(ids, list) and all(isinstance(x, str) and re.fullmatch(r'L-[0-9]{4,}', x)
                                            for x in ids), 'lessons_provided 证据损坏')
        if ids:
            rows.append((row.get('call'), ids))
    return rows


def _spec_fix(state):
    if isinstance(state, dict):
        return any(key in {'status', 'code', 'attribution'} and value == 'NEEDS_SPEC_FIX'
                   or _spec_fix(value) for key, value in state.items())
    return isinstance(state, list) and any(_spec_fix(value) for value in state)


def detect_signals(project: Path, change: dict, state: dict) -> list[dict]:
    """Locate problem signals in run history and diagnostic call evidence."""
    signals = []
    def add(kind, where):
        signals.append({'kind': kind, 'where': where})
    failures = state.get('failures') or []
    if failures and state.get('status') == 'PASSED':
        add('failed_then_fixed', 'failures')
    for field, kind in (('repairs', 'acceptance_repair'), ('resets', 'reset'), ('answers', 'human_answer')):
        if state.get(field):
            add(kind, field)
    counts = Counter(identity for failure in failures for identity in {
        _review_identity(f) for f in (failure.get('review') or {}).get('findings', [])})
    if any(count >= 2 for count in counts.values()):
        add('repeated_review_finding', 'failures[].review.findings')
    for field in ('failures', 'stop'):
        if _spec_fix(state.get(field)):
            add('needs_spec_fix', field)
    previous = (change.get('supersedes') or {}).get('change_id')
    if previous:
        previous = identifier(previous, 'supersedes change')
        path = safe_target(project, f'.storyloop/state/runs/{previous}.json', 'previous run')
        old = load_json(path)
        if _spec_fix(old.get('failures')) or _spec_fix(old.get('stop')):
            add('needs_spec_fix', f'{previous}:failures/stop')
    if failures:
        for call, _ in _provided(project, change['change_id']):
            add('provided_then_failed', call)
    return signals


def record_recurrence(project: Path, change_id: str, state: dict) -> list[str]:
    """Count each change once; caller owns the project lock."""
    if not state.get('failures'):
        return []
    ids = {item for _, items in _provided(project, change_id) for item in items}
    path = safe_target(project, '.storyloop/state/lessons.json', 'recurrence state')
    data = load_json(path) if path.is_file() else {}
    for lesson_id in sorted(ids):
        entry = data.setdefault(lesson_id, {'recurrences': []})
        values = entry.get('recurrences')
        require(isinstance(values, list) and all(isinstance(x, str) for x in values), '复发记录损坏')
        if change_id not in values:
            values.append(change_id)
    if ids:
        write_json(path, data)
    return sorted(item for item in ids if len(set(data[item]['recurrences'])) >= 2)


def _payload(project, change, signals, upgrades):
    commit = _git(project, 'rev-parse', 'HEAD').strip()
    entries = load_lessons_at(project, commit)
    stale = stale_ids(project, entries, commit)
    lessons = [{key: item[key] for key in ('id', 'summary', 'strength')} | {
        'path': f"openspec/lessons/{item['id']}.md", 'status': effective_status(item, stale)} for item in entries]
    foundation = safe_target(project, 'openspec/foundation', 'foundation', target_directory=True)
    documents = [p.relative_to(project).as_posix() for p in foundation.rglob('*.md') if not p.is_symlink()]
    principles_path = safe_target(project, 'openspec/principles.json', 'principles')
    principles = load_json(principles_path).get('principles', []) if principles_path.is_file() else []
    return {'change_id': change['change_id'], 'mode': 'retrospective', 'signals': signals,
            'lessons': lessons, 'foundation_documents': sorted(documents),
            'principles': [p['id'] for p in principles], 'upgrade_required': upgrades}


def _validate_proposal(project, proposal, known):
    require(isinstance(proposal, dict), '经验提议必须是对象')
    required = {'action', 'entry', 'body', 'why_needed', 'evidence'}
    require(required <= proposal.keys() <= required | {'target', 'landing'}, '经验提议字段缺失或未知')
    action, target = proposal.get('action'), proposal.get('target')
    require(action in ACTIONS, 'action 无效')
    require(target is None if action == 'create' else target in known, 'target 无效')
    why = proposal.get('why_needed')
    require(isinstance(why, str) and bool(why.strip()), 'why_needed 不能为空')
    evidence = proposal.get('evidence')
    require(isinstance(evidence, list) and bool(evidence)
            and all(valid_location(project, x) for x in evidence), 'evidence 必须指向真实源码行')
    entry, body = proposal.get('entry'), proposal.get('body')
    require(isinstance(entry, dict) and 'id' not in entry, 'entry 必须是不含 id 的元数据')
    from .role_schemas import RETROSPECTIVE_ENTRY
    require(set(entry) == set(RETROSPECTIVE_ENTRY['properties']), 'entry 字段缺失或未知')
    require(isinstance(body, str), 'body 必须是文本')
    # Synthetic identity is only a field-validation adapter; no ID is allocated here.
    parsed = parse_lesson(render_lesson({**entry, 'id': 'L-0000', 'body': body}), 'L-0000.md')
    require(set(parsed['roles']) <= set(ROLES), 'entry.roles 无效')
    _validate_landing(proposal.get('landing'))
    return {key: proposal.get(key) for key in ('action', 'target', 'entry', 'why_needed', 'evidence', 'landing')}, body


def _validate_landing(landing):
    if landing is None:
        return
    require(isinstance(landing, dict), 'landing 必须是对象或 null')
    kind = landing.get('kind')
    fields = {'foundation': {'kind', 'path', 'append'},
              'check_command': {'kind', 'argv'}, 'principles': {'kind', 'principle', 'change'}}
    require(isinstance(kind, str) and kind in LANDING_KINDS and kind in fields, 'landing.kind 无效')
    require(set(landing) == fields[kind], 'landing 字段缺失或未知')
    if kind == 'foundation':
        path = repository_relative(landing['path'], 'landing.path')
        require(path.startswith('openspec/foundation/'), 'landing.path 必须位于知识库')
        require(isinstance(landing['append'], str) and bool(landing['append'].strip()), 'landing.append 无效')
    elif kind == 'check_command':
        argv = landing['argv']
        require(isinstance(argv, list) and bool(argv)
                and all(isinstance(x, str) and bool(x.strip()) for x in argv), 'landing.argv 无效')
    else:
        from .principles import validate_principles
        validate_principles({'principles': [landing['principle']]})
        require(isinstance(landing['change'], str) and bool(landing['change'].strip()), 'landing.change 无效')


def _concept(item, change_id, commit):
    require(isinstance(item, dict), 'uncovered_concept 必须是对象')
    require(set(item) == {'concept', 'evidence', 'suggested_document'}, 'uncovered_concept 字段缺失或未知')
    concept = item.get('concept')
    require(isinstance(concept, str) and bool(concept.strip()) and '\n' not in concept, 'concept 无效')
    path = repository_relative(item.get('suggested_document'), 'suggested_document')
    require(path.startswith('openspec/foundation/'), 'suggested_document 必须位于知识库')
    evidence = item.get('evidence')
    body = f'## {concept}\n\n（待补充，证据：{evidence}）'
    return {'action': 'create', 'target': None, 'body': body,
            'why_needed': f'缺少 {concept} 时，冷读代码的 AI 无法获知证据中揭示的约束。',
            'evidence': evidence, 'landing': {'kind': 'foundation', 'path': path, 'append': body},
            'entry': {'summary': concept, 'tier': 'normal', 'scope': ['**'], 'roles': ['implement', 'review'],
                      'strength': 'knowledge', 'source': change_id, 'baseline_commit': commit,
                      'status': 'active', 'updated_at': date.today().isoformat(), 'supersedes': [],
                      'landings': [{'kind': 'foundation', 'path': path}]}}


def _drafts(project, change_id, response, payload, result, proposer=None):
    known = {entry['id'] for entry in payload['lessons']}
    proposals = [(False, item) for item in response.get('lessons', [])]
    proposals += [(True, item) for item in response.get('uncovered_concepts', [])]
    commit = _git(project, 'rev-parse', 'HEAD').strip()
    for index, (concept, proposal) in enumerate(proposals):
        try:
            require(index < 3, '超过每次复盘 3 条经验提议上限')
            if concept:
                proposal = _concept(proposal, change_id, commit)
            metadata, body = _validate_proposal(project, proposal, known)
            draft = create_draft(project, 'lesson', change_id, proposer, metadata, body)
            result['drafts'].append('lesson/' + draft)
        except (StoryLoopError, ValueError, TypeError, OSError) as exc:
            result.setdefault('dropped', []).append({'index': index, 'reason': str(exc)})


def _successful_calls(before, rows):
    """Return calls created and completed successfully by this invocation."""
    return [row for row in rows if isinstance(row.get('call'), str)
            and row['call'] not in before and not row.get('failed')]


def _perform(project, change, state, manual=False):
    result = {'retrospective': 'FAILED', 'signals': [], 'drafts': [], 'upgrade_suggested': [], 'error': None}
    try:
        result['signals'] = detect_signals(project, change, state)
        result['upgrade_suggested'] = record_recurrence(project, change['change_id'], state)
        if not result['signals'] and not manual:
            result['retrospective'] = 'SKIPPED_NO_SIGNAL'
            return result
        check_before_call(project, change['change_id'], load_project_config(project))
        payload = _payload(project, change, result['signals'], result['upgrade_suggested'])
        before = {row.get('call') for row in ledger_entries(project, change['change_id'])}
        response = read_only_call(project, change['change_id'],
                                  lambda: invoke_project_agent(project, 'review', payload))
        rows = ledger_entries(project, change['change_id'])
        calls = _successful_calls(before, rows)
        proposer = calls[-1]['call'] if calls else None
        _drafts(project, change['change_id'], response, payload, result, proposer)
        # Errata are already captured exactly once by invoke, after the guard succeeded.
        for row in calls:
            meta = load_json(_meta_path(project, row['call']))
            errata = meta.get('errata') or {}
            result['drafts'].extend('errata/' + ref for ref in errata.get('drafts', []))
            result.setdefault('dropped', []).extend(errata.get('dropped', []))
        result['retrospective'] = 'DONE'
    except Exception as exc:
        result['retrospective'] = ('SKIPPED_COST_LIMIT' if isinstance(exc, StoryLoopError)
                                  and exc.code == 'COST_LIMIT_REACHED' else 'FAILED')
        result['error'] = str(exc)
    return result


def retrospect(project: Path, change_id: str, *, manual: bool = False) -> dict:
    """Run a manual terminal retrospective while holding the project lock."""
    project = Path(project).resolve()
    change_id = identifier(change_id, 'change id')
    with locked(project):
        change = load_change(project, change_id)
        state = load_json(safe_target(project, f'.storyloop/state/runs/{change_id}.json', 'run'))
        allowed = {'PASSED', 'SUPERSEDED'} | STOP_STATUSES if manual else {'PASSED'}
        require(state.get('status') in allowed, '只能复盘已完成或停点变更')
        state['lessons'] = _perform(project, change, state, manual)
        save_run(project, state)
        return state['lessons']


def retrospect_after_pass(project: Path, change: dict, state: dict) -> None:
    """Called after receipt/save with the run lock held; never change the verdict."""
    require(state.get('status') == 'PASSED', '自动复盘只用于 PASSED')
    state['lessons'] = _perform(project, change, state)
