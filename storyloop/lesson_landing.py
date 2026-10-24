"""Approve human memory drafts with scoped Git transactions."""
from datetime import date
import hashlib
import json
from pathlib import Path
import tempfile

from .baseline import _git, load_baseline
from .base_upgrade import propose_base_change
from .drafts import load_draft, set_draft_status
from .lessons import next_lesson_id, parse_lesson, render_lesson, validate_lessons
from .lesson_store import (
    _resolve_commit, _validate_landings_at, load_lessons_at, require_idle_source,
)
from .models import StoryLoopError, identifier, require, text
from .path_security import safe_target, repository_relative
from .principles import validate_principles
from .processutil import run_process
from .project_profile import validate_project_settings
from .project_git import changed_paths, commit_paths
from .storage import load_json, locked, write_bytes_atomic, write_json


PROFILE_SETTING_KEYS = {
    'test_commands', 'unit_commands', 'integration_commands', 'check_commands',
    'test_roots', 'module_roots', 'integration_paths', 'candidate_allow',
    'scratch_paths', 'test_count_patterns', 'stacks',
}


def approve_draft(project: Path, ref: str) -> dict:
    """Land a pending draft or finish its previously committed transaction."""
    project = Path(project).resolve()
    with locked(project):
        draft = load_draft(project, ref)
        base_pending = _base_landing_path(project, ref)
        if base_pending.is_file():
            record = load_json(base_pending)
            require(record.get('draft_digest') == _draft_digest(draft)
                    and record.get('source_head') == _git(project, 'rev-parse', 'HEAD').strip(),
                    '待处理的 BASE 草案与当前知识草案不一致')
            _validate_base_landing(project, record)
            return record['result']
        pending = _landing_path(project, ref)
        _require_no_other_landing(project, pending)
        require(draft.get('status') == 'pending' or (
            pending.is_file() and draft.get('status') == 'approved'), '只能审批 pending 草案')
        try:
            require_idle_source(project)
            if pending.exists():
                return _resume_landing(project, ref, draft, pending)
            if draft['kind'] == 'errata':
                from .errata import land_errata
                return land_errata(project, draft)
            return _approve_lesson(project, ref, draft)
        except Exception as exc:
            if draft.get('status') == 'pending':
                set_draft_status(project, ref, 'pending', str(exc))
            raise


def _candidate(project, draft, head):
    payload = draft.get('payload')
    require(isinstance(payload, dict), '草案 payload 必须是对象')
    action = payload.get('action')
    require(action in {'create', 'update', 'retire'}, '无效经验 action')
    text(payload.get('why_needed'), 'why_needed')
    evidence = payload.get('evidence')
    require(isinstance(evidence, list) and all(isinstance(x, str) and x.strip() for x in evidence), '无效 evidence')
    entries = load_lessons_at(project, head)
    target = payload.get('target')
    old = next((e for e in entries if e['id'] == target), None)
    require(target is None if action == 'create' else old is not None, '经验 target 无效')
    metadata = payload.get('entry')
    require(isinstance(metadata, dict) and 'id' not in metadata, 'entry 必须是无 id 的元数据对象')
    entry = dict(old) if action == 'retire' else dict(metadata)
    entry.update(id=next_lesson_id(entries) if action == 'create' else target,
                 source=draft['source'], baseline_commit=head, updated_at=date.today().isoformat())
    if action == 'retire':
        entry['status'] = 'retired'
    elif draft['body'] is not None:
        entry['body'] = draft['body']
    strength = entry.get('strength')
    if strength == 'config' and action != 'retire' and not (
            action == 'update' and old['strength'] == 'config'):
        raise StoryLoopError('config 强度本期不自动落地：请把草案改为 prose，并手工修改配置', code='LANDING_UNSUPPORTED')
    argv = None
    if strength == 'check' and action != 'retire':
        argv = _check_argv(project, draft, head)
        entry['landings'] = [{'kind': 'check_command', 'argv': argv}]
    writes = land_knowledge(project, draft, entry) if strength == 'knowledge' and action != 'retire' else land_prose(entry)
    relative = f"openspec/lessons/{entry['id']}.md"
    entry = parse_lesson(render_lesson(entry), relative)
    from .project_agents import ROLES
    validate_lessons([e for e in entries if e['id'] != entry['id']] + [entry], set(ROLES))
    frozen_refs = [landing for landing in entry['landings']
                   if landing.get('path', 'openspec/principles.json') not in writes]
    _validate_landings_at(project, head, [{**entry, 'landings': frozen_refs}])
    writes[relative] = render_lesson(entry).encode('utf-8')
    return entry, writes, argv


def _check_argv(project: Path, draft: dict, green_commit: str) -> list[str]:
    landing = draft['payload'].get('landing')
    require(isinstance(landing, dict) and landing.get('kind') == 'check_command',
            'check 落点必须是 check_command')
    argv = landing.get('argv')
    validated = validate_project_settings(project, {'check_commands': [argv]})
    source = draft.get('source')
    if source == 'manual':
        raise StoryLoopError('无法找到证明会红的提交', code='CHECK_NOT_PROVEN')
    source = identifier(source, 'draft source')
    run_path = project / '.storyloop/state/runs' / f'{source}.json'
    state = load_json(run_path) if run_path.is_file() else {}
    red_commit = state.get('contract_commit')
    if not isinstance(red_commit, str) or not red_commit:
        raise StoryLoopError('无法找到证明会红的提交', code='CHECK_NOT_PROVEN')
    prove_check(project, validated['check_commands'][0], red_commit, green_commit)
    return validated['check_commands'][0]


def _run_check_at(project: Path, argv: list[str], commit: str, timeout: int) -> dict:
    with tempfile.TemporaryDirectory(prefix='storyloop-check-') as temporary:
        checkout = Path(temporary) / 'checkout'
        original_error = None
        try:
            _git(project, 'worktree', 'add', '--detach', str(checkout), commit)
            return run_process(argv, checkout, timeout)
        except BaseException as exc:
            original_error = exc
            raise
        finally:
            try:
                _git(project, 'worktree', 'remove', '--force', str(checkout))
            except BaseException:
                if original_error is None:
                    raise


def prove_check(project: Path, argv: list[str], red_commit: str,
                green_commit: str, timeout: int = 600) -> dict:
    """Prove an argv check fails before the change and passes at current code."""
    project = Path(project).resolve()
    argv = validate_project_settings(project, {'check_commands': [argv]})['check_commands'][0]
    red_commit = _resolve_commit(project, red_commit)
    green_commit = _resolve_commit(project, green_commit)
    red = _run_check_at(project, argv, red_commit, timeout)
    green = _run_check_at(project, argv, green_commit, timeout)
    invalid = lambda value: (
        value.get('contained') is not True or value.get('timed_out')
        or value.get('truncated') or value.get('error')
        or not isinstance(value.get('returncode'), int)
    )
    if invalid(red) or invalid(green) or red['returncode'] == 0 or green['returncode'] != 0:
        raise StoryLoopError('check 未能证明在合同提交上为红、当前提交上为绿',
                             code='CHECK_NOT_PROVEN')
    return {
        'red': {'commit': red_commit, 'exit_code': red['returncode']},
        'green': {'commit': green_commit, 'exit_code': green['returncode']},
    }


def land_prose(entry: dict) -> dict:
    """Prepare the prose-only landing; metadata is validated by the caller."""
    return {}


def land_knowledge(project, draft, entry) -> dict:
    """Prepare foundation/principles bytes without touching the repository."""
    landing = draft['payload'].get('landing')
    require(isinstance(landing, dict), 'knowledge 必须声明 landing')
    kind = landing.get('kind')
    if kind == 'foundation':
        relative = repository_relative(landing.get('path'), 'foundation path')
        require(relative.startswith('openspec/foundation/'), 'foundation path 必须位于 openspec/foundation/')
        target = safe_target(project, relative, 'foundation landing')
        append = text(landing.get('append'), 'foundation append')
        original = target.read_bytes() if target.exists() else b''
        content = original + (b'\n' if original and not original.endswith(b'\n') else b'') + append.encode('utf-8') + b'\n'
        entry['landings'] = [{'kind': kind, 'path': relative}]
    elif kind == 'principles':
        relative = 'openspec/principles.json'
        target = safe_target(project, relative, 'principles landing')
        value = json.loads(target.read_text(encoding='utf-8')) if target.exists() else {'principles': []}
        meta = validate_principles(value)
        principle = landing.get('principle')
        validate_principles({'principles': [principle]})
        change = text(landing.get('change'), 'principles change')
        value['principles'] = [p for p in value['principles'] if p['id'] != principle['id']] + [principle]
        major, minor, patch = map(int, meta['version'].split('.'))
        value['version'] = f'{major}.{minor}.{patch + 1}'
        value['changelog'] = meta['changelog'] + [dict(version=value['version'], date=date.today().isoformat(), change=change, source=draft['source'])]
        content = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        entry['landings'] = [{'kind': kind}]
    else:
        raise StoryLoopError('knowledge 落点必须是 foundation 或 principles')
    return {relative: content}


def _proposal_paths(project, writes):
    source = load_baseline(project)['source_head']
    changed = changed_paths(project, source, 'HEAD')
    require(all(p.startswith('openspec/lessons/') for p in changed), 'BASE 后存在非经验变更，请先处理 BASE')
    return sorted(changed | set(writes))


def _snapshot(project, writes, include_config=False):
    index = Path(_git(project, 'rev-parse', '--git-path', 'index').strip())
    if not index.is_absolute():
        index = project / index
    originals = {safe_target(project, p, 'lesson landing'): None for p in writes}
    if include_config:
        originals[safe_target(project, '.storyloop/config.json', 'project config')] = None
    originals[index] = None
    for path in originals:
        originals[path] = path.read_bytes() if path.exists() else None
    return originals


def _own_commit(project, parent, tree, message):
    candidate = _git(project, 'rev-parse', 'HEAD').strip()
    if candidate == parent or tree is None:
        return None
    # NUL fields preserve legal Unicode line separators inside the subject.
    actual = _git(project, 'show', '-s', '--format=%P%x00%T%x00%s', candidate).rstrip('\n').split('\0')
    return candidate if actual == [parent, tree, message] else None


def _restore(project, originals, parent, proposal):
    current = _git(project, 'rev-parse', 'HEAD').strip()
    require(current == parent, '回滚受阻：外部 HEAD 已推进，保留当前提交和文件')
    for path, raw in originals.items():
        if raw is None:
            path.unlink(missing_ok=True)
        else:
            write_bytes_atomic(path, raw)
    if proposal:
        proposal_path = Path(proposal['proposal'])
        state_path = safe_target(project, f".storyloop/state/changes/{proposal['change_id']}.json",
                                 'BASE draft')
        if state_path.exists():
            state = load_json(state_path)
            require(state.get('status') == 'DRAFT'
                    and state.get('product', {}).get('base', {}).get('paths') == proposal['paths']
                    and (not proposal_path.exists() or state.get('base_proposal_sha256')
                         == hashlib.sha256(proposal_path.read_bytes()).hexdigest()),
                    '回滚受阻：BASE 草案已变化，保留提案与草案')
            state_path.unlink()
        proposal_path.unlink(missing_ok=True)


def _approve_lesson(project, ref, draft):
    parent = _git(project, 'rev-parse', 'HEAD').strip()
    branch = _git(project, 'symbolic-ref', '--quiet', 'HEAD').strip()
    try:
        entry, writes, check_argv = _candidate(project, draft, parent)
    except (ValueError, TypeError, KeyError) as exc:
        raise StoryLoopError('草案元数据或落点结构无效', code='LESSON_INVALID') from exc
    return land_transaction(project, ref, writes,
                            'chore(lessons): ' + entry['summary'],
                            {'lesson_id': entry['id']}, parent=parent, draft=draft,
                            branch=branch, check_argv=check_argv,
                            base_reason=draft['payload']['why_needed'] if len(writes) > 1 else None)


def land_transaction(project, ref, writes, message, result_ids,
                     *, parent, draft, branch, check_argv=None, base_reason=None):
    """Roll back only uncommitted writes; preserve committed work for durable recovery."""
    require(_git(project, 'rev-parse', 'HEAD').strip() == parent, '校验期间 HEAD 已推进')
    _require_landing_source(project, ref, draft, branch)
    originals = _snapshot(project, writes, include_config=check_argv is not None)
    proposal, own, tree, record = None, None, None, None
    pending = _landing_path(project, ref)
    # Match Git's trailing ASCII-whitespace cleanup before binding commit identity.
    message = message.rstrip(' \t\r\n\v\f')
    try:
        if base_reason is not None:
            proposal = propose_base_change(project, _proposal_paths(project, writes), base_reason,
                                           scope="knowledge")
        for relative, content in writes.items():
            write_bytes_atomic(safe_target(project, relative, 'lesson landing'), content)
        if proposal is not None:
            result = dict(status='BASE_APPROVAL_REQUIRED', **result_ids, commit=None,
                          paths=sorted(writes), base_proposal=proposal['change_id'],
                          next=proposal['next'])
            write_json(_base_landing_path(project, ref),
                       {'version': 1, 'ref': ref, 'draft_digest': _draft_digest(draft),
                        'source_head': parent, 'result': result}, max_bytes=512_000)
            _validate_base_landing(project, {'result': result})
            return result
        _git(project, 'add', '--', *writes)
        tree = _git(project, 'write-tree').strip()
        _require_landing_source(project, ref, draft, branch)
        record = dict(version=1, ref=ref, draft_digest=_draft_digest(draft),
                      parent=parent, tree=tree, message=message, paths=sorted(writes),
                      branch=branch,
                      result_ids=result_ids, check_argv=check_argv, proposal=proposal)
        write_json(pending, record, max_bytes=512_000)
        commit_paths(project, list(writes), message)
        own = _own_commit(project, parent, tree, message)
        require(own is not None, '提交身份无法确认')
        return _resume_landing(project, ref, load_draft(project, ref), pending)
    except Exception:
        own = own or _own_commit(project, parent, tree, message)
        if own is not None:
            require(_git(project, 'rev-parse', 'HEAD').strip() == own,
                    '外部 HEAD 已推进，保留当前提交、文件与待恢复记录')
        else:
            _restore(project, originals, parent, proposal)
            pending.unlink(missing_ok=True)
            _base_landing_path(project, ref).unlink(missing_ok=True)
        raise


def _base_landing_path(project: Path, ref: str) -> Path:
    return project / '.storyloop/state/base-landings' / (hashlib.sha256(ref.encode('utf-8')).hexdigest() + '.json')


def _validate_base_landing(project: Path, record: dict) -> None:
    result = record.get('result') or {}
    require(result.get('status') == 'BASE_APPROVAL_REQUIRED'
            and isinstance(result.get('paths'), list)
            and isinstance(result.get('base_proposal'), str), '待处理的 BASE 落点记录无效')
    proposal_id = identifier(result['base_proposal'], 'BASE 提案标识')
    proposal_path = safe_target(project, f'.storyloop/base-proposals/{proposal_id}.json', 'BASE 提案')
    require(proposal_path.is_file(), 'BASE 提案已变化或丢失')
    proposal = load_json(proposal_path)
    approved = {item['path'] for item in proposal.get('changes') or []}
    require(proposal.get('change_id') == proposal_id
            and set(result['paths']) <= approved, 'BASE 提案与知识候选路径不一致')
    if 'errata_id' in result:
        content = (project / 'openspec/foundation/errata.md').read_text(encoding='utf-8')
        ids = [line[3:] for line in content.splitlines() if line.startswith('## E-')]
        require(bool(ids) and ids[-1] == result['errata_id'], '勘误标识与待处理的 BASE 候选不一致')
    if 'lesson_id' in result:
        require((project / 'openspec/lessons' / f"{result['lesson_id']}.md").is_file(),
                '经验标识与待处理的 BASE 候选不一致')


def _landing_path(project, ref):
    repository_relative(ref, 'draft reference')
    return safe_target(project, f'.storyloop/state/memory-landings/{ref}.json', 'memory landing')


def _require_no_other_landing(project, pending):
    root = safe_target(project, '.storyloop/state/memory-landings', 'memory landings', target_directory=True)
    others = [path for path in root.glob('*/*.json') if path != pending] if root.is_dir() else []
    require(not others, '存在未完成的记忆落地，请先恢复原草案')


def _draft_digest(draft):
    stable = {key: value for key, value in draft.items() if key not in {'status', 'failure'}}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def _require_landing_source(project, ref, draft, branch):
    require(_draft_digest(load_draft(project, ref)) == _draft_digest(draft),
            '草案已变化，保留现场，请重新核查')
    require(_git(project, 'symbolic-ref', '--quiet', 'HEAD').strip() == branch,
            '记忆落地分支已变化，保留现场')


def _load_landing(project, path, ref, draft):
    require(path.stat().st_size <= 512_000, '记忆落地恢复记录过大')
    record = load_json(path)
    keys = {'version', 'ref', 'draft_digest', 'parent', 'tree', 'message', 'paths',
            'branch', 'result_ids', 'check_argv', 'proposal'}
    require(set(record) == keys and type(record['version']) is int and record['version'] == 1
            and record['ref'] == ref, '记忆落地恢复记录无效')
    require(record['draft_digest'] == _draft_digest(draft), '草案已变化，保留原提交，请人工核查')
    for field in ('parent', 'tree'):
        value = record[field]
        require(isinstance(value, str) and len(value) in {40, 64}
                and all(c in '0123456789abcdef' for c in value), '记忆落地提交指纹无效')
    text(record['message'], 'landing message')
    require(isinstance(record['branch'], str) and record['branch'].startswith('refs/heads/'),
            '记忆落地分支无效')
    paths = record['paths']
    require(isinstance(paths, list) and paths and all(isinstance(p, str) for p in paths),
            '记忆落地路径无效')
    require(paths == sorted(set(paths)), '记忆落地路径重复或无序')
    for relative in paths:
        repository_relative(relative, 'landing path')
        require(relative.startswith(('openspec/lessons/', 'openspec/foundation/'))
                or relative == 'openspec/principles.json', '记忆落地路径越界')
    _validate_landing_payload(project, record, draft)
    return record


def _validate_landing_payload(project, record, draft):
    kind = draft['kind']
    ids = record['result_ids']
    key = 'lesson_id' if kind == 'lesson' else 'errata_id'
    require(isinstance(ids, dict) and set(ids) == {key}, '记忆落地结果标识无效')
    identifier(ids[key], key)
    if kind == 'lesson':
        require(f"openspec/lessons/{ids[key]}.md" in record['paths'], '记忆落地标识与提交路径不一致')
    else:
        require(record['paths'] == ['openspec/foundation/errata.md'], '勘误提交路径无效')
        diff = _git(project, 'diff', '--unified=0', record['parent'], record['tree'],
                    '--', 'openspec/foundation/errata.md')
        require(f"+## {ids[key]}" in diff.splitlines(), '勘误标识与本次新增条目不一致')
    payload = draft.get('payload', {})
    needs_check = (kind == 'lesson' and payload.get('action') != 'retire'
                   and payload.get('entry', {}).get('strength') == 'check')
    expected = (payload.get('landing') or {}).get('argv') if needs_check else None
    require(record['check_argv'] == expected,
            '记忆落地配置与批准草案不一致')
    _validate_landing_proposal(project, record)


def _validate_landing_proposal(project, record):
    proposal = record['proposal']
    needed = any(not path.startswith('openspec/lessons/') for path in record['paths'])
    require((proposal is not None) == needed, '记忆落地 BASE 提案缺失或多余')
    if proposal is None:
        return
    require(isinstance(proposal, dict) and set(proposal) == {
        'status', 'change_id', 'proposal', 'paths', 'next'}, '记忆落地 BASE 提案无效')
    proposal_id = identifier(proposal['change_id'], 'BASE 提案标识')
    path = safe_target(project, f'.storyloop/base-proposals/{proposal_id}.json', 'BASE 提案')
    require(proposal['status'] == 'PROPOSED' and proposal['proposal'] == str(path)
            and path.is_file(), '记忆落地 BASE 提案已变化或丢失')
    paths = proposal['paths']
    require(isinstance(paths, list) and all(isinstance(p, str) for p in paths)
            and paths == sorted(set(paths)) and set(record['paths']) <= set(paths),
            '记忆落地 BASE 提案路径无效')
    text(proposal['next'], 'BASE 提案 next')
    actual = load_json(path)
    changes = actual.get('changes')
    require(isinstance(changes, list) and all(isinstance(c, dict) for c in changes), 'BASE 提案内容无效')
    baseline = load_baseline(project)
    require(actual.get('change_id') == proposal_id and actual.get('status') == 'PROPOSED'
            and [c.get('path') for c in changes] == paths
            and actual.get('baseline_version') == baseline['baseline_version']
            and actual.get('baseline_source_head') == baseline['source_head'],
            '记忆落地 BASE 提案内容或底座已变化')


def _resume_landing(project, ref, draft, pending):
    record = _load_landing(project, pending, ref, draft)
    require(_git(project, 'symbolic-ref', '--quiet', 'HEAD').strip() == record['branch'],
            '记忆落地恢复分支已变化，保留现场')
    require_idle_source(project)
    own = _own_commit(project, record['parent'], record['tree'], record['message'])
    require(own is not None, '记忆落地 HEAD 与已提交事务不一致，保留现场')
    paths = sorted(filter(None, _git(project, 'diff-tree', '--no-commit-id', '--name-only',
                                   '-r', '-z', own).split('\0')))
    require(paths == record['paths'], '记忆落地提交路径与恢复记录不一致')
    return _finish_landing(project, ref, record, own, pending)


def _finish_landing(project, ref, record, own, pending):
    proposal = record['proposal']
    result = dict(status='APPLIED', **record['result_ids'], commit=own,
                  base_proposal=proposal['change_id'] if proposal else None,
                  next=proposal['next'] if proposal else '经验已落地')
    if record['check_argv'] is not None:
        _land_check_config(project, record['check_argv'])
    set_draft_status(project, ref, 'approved')
    pending.unlink()
    return result


def _land_check_config(project: Path, argv: list[str]) -> None:
    path = safe_target(project, '.storyloop/config.json', 'project config')
    config = load_json(path)
    profile = config.get('profile')
    require(isinstance(profile, dict), '项目 profile 无效')
    commands = list(profile.get('check_commands') or [])
    if argv not in commands:
        commands.append(argv)
    settings = {key: profile[key] for key in PROFILE_SETTING_KEYS if key in profile}
    settings['check_commands'] = commands
    validated = validate_project_settings(project, settings)
    profile['check_commands'] = validated['check_commands']
    write_json(path, config, max_bytes=512_000)
