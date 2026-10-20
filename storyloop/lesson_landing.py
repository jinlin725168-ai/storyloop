"""Approve human memory drafts with scoped Git transactions."""
from datetime import date
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
from .project_git import commit_paths
from .storage import load_json, locked, write_bytes_atomic, write_json


PROFILE_SETTING_KEYS = {
    'test_commands', 'unit_commands', 'integration_commands', 'check_commands',
    'test_roots', 'module_roots', 'integration_paths', 'candidate_allow',
    'scratch_paths', 'test_count_patterns', 'stacks',
}


def approve_draft(project: Path, ref: str) -> dict:
    """Validate and atomically land one pending memory draft."""
    project = Path(project).resolve()
    with locked(project):
        draft = load_draft(project, ref)
        require(draft.get('status') == 'pending', '只能审批 pending 草案')
        try:
            require_idle_source(project)
            if draft['kind'] == 'errata':
                raise StoryLoopError('勘误落地尚未实现', code='LANDING_UNSUPPORTED')
            return _approve_lesson(project, ref, draft)
        except Exception as exc:
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
    if strength == 'config':
        raise StoryLoopError('config 强度本期不自动落地：请把草案改为 prose，并手工修改配置', code='LANDING_UNSUPPORTED')
    if strength == 'check':
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
    return entry, writes, argv if strength == 'check' else None


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
    changed = set(filter(None, _git(project, 'diff', '--name-only', '-z', source, 'HEAD').split('\0')))
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


def _restore(project, originals, parent, own, proposal):
    current = _git(project, 'rev-parse', 'HEAD').strip()
    if current != parent:
        if own is None or current != own:
            raise StoryLoopError('回滚受阻：外部 HEAD 已推进，保留当前提交和文件')
        # Compare-and-swap prevents rewinding an independently advanced branch.
        _git(project, 'update-ref', 'HEAD', parent, own)
    for path, raw in originals.items():
        if raw is None:
            path.unlink(missing_ok=True)
        else:
            write_bytes_atomic(path, raw)
    if proposal:
        Path(proposal['proposal']).unlink(missing_ok=True)


def _approve_lesson(project, ref, draft):
    parent = _git(project, 'rev-parse', 'HEAD').strip()
    try:
        entry, writes, check_argv = _candidate(project, draft, parent)
    except (ValueError, TypeError, KeyError) as exc:
        raise StoryLoopError('草案元数据或落点结构无效', code='LESSON_INVALID') from exc
    originals = _snapshot(project, writes, include_config=check_argv is not None)
    proposal, own, tree = None, None, None
    # Match Git's trailing ASCII-whitespace cleanup before binding commit identity.
    message = ('chore(lessons): ' + entry['summary']).rstrip(' \t\r\n\v\f')
    try:
        if len(writes) > 1:
            proposal = propose_base_change(project, _proposal_paths(project, writes), draft['payload']['why_needed'])
        for relative, content in writes.items():
            write_bytes_atomic(safe_target(project, relative, 'lesson landing'), content)
        _git(project, 'add', '--', *writes)
        tree = _git(project, 'write-tree').strip()
        commit_paths(project, list(writes), message)
        own = _own_commit(project, parent, tree, message)
        require(own is not None, '提交身份无法确认')
        if check_argv is not None:
            _land_check_config(project, check_argv)
        set_draft_status(project, ref, 'approved')
    except Exception:
        own = own or _own_commit(project, parent, tree, message)
        _restore(project, originals, parent, own, proposal)
        raise
    return dict(status='APPLIED', lesson_id=entry['id'], commit=own,
                base_proposal=proposal['change_id'] if proposal else None,
                next=proposal['next'] if proposal else '经验已落地')


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
