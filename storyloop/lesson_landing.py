"""Approve human memory drafts with scoped Git transactions."""
from datetime import date
import json
from pathlib import Path

from .baseline import _git, load_baseline
from .base_upgrade import propose_base_change
from .drafts import load_draft, set_draft_status
from .lessons import next_lesson_id, parse_lesson, render_lesson, validate_lessons
from .lesson_store import load_lessons_at, require_idle_source, _validate_landings_at
from .models import StoryLoopError, require, text
from .path_security import safe_target, repository_relative
from .principles import validate_principles
from .project_git import commit_paths
from .storage import locked, write_bytes_atomic


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
        raise StoryLoopError('check 强度落地尚未实现', code='LANDING_UNSUPPORTED')
    writes = land_knowledge(project, draft, entry) if strength == 'knowledge' and action != 'retire' else land_prose(entry)
    relative = f"openspec/lessons/{entry['id']}.md"
    entry = parse_lesson(render_lesson(entry), relative)
    from .project_agents import ROLES
    validate_lessons([e for e in entries if e['id'] != entry['id']] + [entry], set(ROLES))
    frozen_refs = [landing for landing in entry['landings']
                   if landing.get('path', 'openspec/principles.json') not in writes]
    _validate_landings_at(project, head, [{**entry, 'landings': frozen_refs}])
    writes[relative] = render_lesson(entry).encode('utf-8')
    return entry, writes


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


def _snapshot(project, writes):
    index = Path(_git(project, 'rev-parse', '--git-path', 'index').strip())
    if not index.is_absolute():
        index = project / index
    originals = {safe_target(project, p, 'lesson landing'): None for p in writes}
    originals[index] = None
    for path in originals:
        originals[path] = path.read_bytes() if path.exists() else None
    return originals


def _own_commit(project, parent, tree, message):
    candidate = _git(project, 'rev-parse', 'HEAD').strip()
    if candidate == parent or tree is None:
        return None
    actual = _git(project, 'show', '-s', '--format=%P%n%T%n%s', candidate).splitlines()
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
        entry, writes = _candidate(project, draft, parent)
    except (ValueError, TypeError, KeyError) as exc:
        raise StoryLoopError('草案元数据或落点结构无效', code='LESSON_INVALID') from exc
    originals = _snapshot(project, writes)
    proposal, own, tree = None, None, None
    message = 'chore(lessons): ' + entry['summary']
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
        set_draft_status(project, ref, 'approved')
    except Exception:
        own = own or _own_commit(project, parent, tree, message)
        _restore(project, originals, parent, own, proposal)
        raise
    return dict(status='APPLIED', lesson_id=entry['id'], commit=own,
                base_proposal=proposal['change_id'] if proposal else None,
                next=proposal['next'] if proposal else '经验已落地')
