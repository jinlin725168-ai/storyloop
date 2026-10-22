"""Validated foundation corrections, captured as drafts and approved through BASE."""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import re

from .drafts import create_draft
from .facts import valid_location
from .models import StoryLoopError, require
from .path_security import repository_relative, safe_target
from .call_log import call_folder
from .storage import load_json, write_json

ERRATA_PATH = 'openspec/foundation/errata.md'
MAX_ERRATA = 5
_PENDING = ContextVar('errata_pending', default=None)


@contextmanager
def defer_errata(*, manual=False):
    """Keep trusted callbacks in memory until the outermost successful guard exits.

    Writer entrypoints use manual release after their boundary audit. A failed nested
    guard discards its own queue; nothing on disk can authorize a deferred callback.
    """
    parent = _PENDING.get()
    current = {'callbacks': [], 'accepted': not manual}
    token = _PENDING.set(current)
    try:
        yield
        if current['accepted']:
            if parent is not None:
                parent['callbacks'].extend(current['callbacks'])
            else:
                for callback in current['callbacks']:
                    callback()
    finally:
        _PENDING.reset(token)


def release_errata():
    """Authorize the current writer scope after its deterministic boundary audit."""
    current = _PENDING.get()
    if current is not None:
        current['accepted'] = True


def _validate(project, item):
    require(isinstance(item, dict), 'errata 必须是对象')
    document = repository_relative(item.get('document'), 'document')
    require(document.startswith('openspec/foundation/') and document != ERRATA_PATH,
            'document 必须是知识库原始文件，不能是 errata.md')
    target = safe_target(project, document, 'document')
    require(target.is_file(), 'document 不存在')
    claim = item.get('claim')
    require(isinstance(claim, str) and bool(claim.strip())
            and claim in target.read_text(encoding='utf-8'), 'claim 必须是文档的精确原文')
    correction = item.get('correction')
    require(isinstance(correction, str) and bool(correction.strip()), 'correction 不能为空')
    evidence = item.get('evidence')
    require(isinstance(evidence, list) and bool(evidence)
            and all(valid_location(project, value) for value in evidence),
            'evidence 必须指向真实源码行')
    return dict(document=document, claim=claim, correction=correction, evidence=list(evidence))


def _meta_path(project, call_id):
    require(isinstance(call_id, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', call_id),
            '无效 call_id')
    return safe_target(project, (call_folder(project, call_id) / 'meta.json').relative_to(project).as_posix(), 'errata call')


def _record(project, call_id, result):
    path = _meta_path(project, call_id)
    meta = load_json(path) if path.is_file() else {}
    meta['errata'] = result
    write_json(path, meta)


def capture_errata(project: Path, response: dict, role: str, call_id: str,
                   source: str | None) -> dict:
    """Drop invalid corrections with diagnostics; queue valid ones until guard success."""
    project = Path(project).resolve()
    _meta_path(project, call_id)
    result = {'drafts': [], 'dropped': []}
    accepted = []
    for index, item in enumerate(response.get('errata') or []):
        try:
            require(index < MAX_ERRATA, '超过每次回答 MAX_ERRATA=5 上限')
            accepted.append((index, _validate(project, item)))
        except (StoryLoopError, ValueError, OSError) as exc:
            result['dropped'].append({'index': index, 'reason': str(exc)})
    _record(project, call_id, result)
    def materialize():
        for index, item in accepted:
            try:
                # Files can have changed between response validation and guard exit.
                item = _validate(project, item)
                result['drafts'].append(create_draft(project, 'errata', source or 'manual',
                                                     call_id, item, None))
            except (StoryLoopError, ValueError, OSError) as exc:
                result['dropped'].append({'index': index, 'reason': str(exc)})
        _record(project, call_id, result)
    current = _PENDING.get()
    if current is None:
        materialize()
    elif accepted:
        current['callbacks'].append(materialize)
    return result


def next_errata_id(text: str) -> str:
    """Allocate the next identifier from actual errata headings."""
    ids = [int(n) for n in re.findall(r'^## E-([0-9]{4,})\s*$', text, re.MULTILINE)]
    return f'E-{max(ids, default=0) + 1:04d}'


def land_errata(project: Path, draft: dict) -> dict:
    """Revalidate an edited draft and append a correction using the shared transaction."""
    from .baseline import _git
    from .lesson_landing import land_transaction
    parent = _git(project, 'rev-parse', 'HEAD').strip()
    branch = _git(project, 'symbolic-ref', '--quiet', 'HEAD').strip()
    item = _validate(project, draft.get('payload'))
    target = safe_target(project, ERRATA_PATH, 'errata landing')
    original = target.read_bytes() if target.exists() else '# 知识库勘误\n'.encode('utf-8')
    errata_id = next_errata_id(original.decode('utf-8'))
    evidence = '、'.join(f'`{value}`' for value in item['evidence'])
    section = (f"\n## {errata_id}\n\n- 文档：`{item['document']}`\n"
               f"- 原结论（已废弃）：> {item['claim']}\n- 新结论：{item['correction']}\n"
               f"- 证据：{evidence}\n- 来源：{draft['source']}（调用 {draft['proposer']}）\n")
    content = original + (b'' if original.endswith(b'\n') else b'\n') + section.encode('utf-8')
    return land_transaction(project, 'errata/' + draft['id'],
                            {ERRATA_PATH: content},
                            f'docs(foundation): 勘误 {errata_id}',
                            {'errata_id': errata_id}, parent=parent, draft=draft,
                            branch=branch, base_reason=item['correction'])
