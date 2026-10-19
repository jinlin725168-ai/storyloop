"""Explicit CLI authorization for changes to skills governing acceptance oracles."""
from contextvars import ContextVar
import hashlib
from pathlib import Path

from .baseline import _git, load_project_config
from .models import require
from .skills import SKILL_TARGETS, SKILLS_ROOT, _frontmatter, _list_field, _skill_text

CONFIRMED = ContextVar('confirmed_oracle_skill_upgrade', default=False)
ORACLE_ROLES = {'product', 'acceptance', 'characterize', 'review'}


def confirmed_upgrade(project: Path, proposal_id: str, confirmed: bool) -> dict:
    """Scope the explicit CLI decision to one normal locked upgrade, preserving its public API."""
    from .base_upgrade import upgrade_baseline
    token = CONFIRMED.set(confirmed is True)
    try:
        return upgrade_baseline(project, proposal_id)
    finally:
        CONFIRMED.reset(token)


def require_confirmation(project: Path, proposal: dict, receipt: dict) -> None:
    """Bind the authorization to both proposal and committed candidate, before regression."""
    affected = _oracle_changes(project, proposal, receipt['baseline_source_head'], receipt['source_head'])
    if not affected:
        return
    approval = {'proposal_digest': receipt['proposal_digest'], 'source_head': receipt['source_head'],
                'candidate_baseline_digest': receipt['candidate_baseline_digest'], 'paths': affected}
    require(CONFIRMED.get() or receipt.get('oracle_skills_approval') == approval,
            'Oracle skills changed; base-update requires --confirm-oracle-skills')
    receipt['oracle_skills_approval'] = approval


def _oracle_changes(project: Path, proposal: dict, old: str, new: str) -> list[str]:
    stacks = load_project_config(project).get('profile', {}).get('stacks', [])
    trees = {commit: set(_git(project, 'ls-tree', '-r', '--name-only', commit).splitlines())
             for commit in (old, new)}
    changed = {}
    for item in proposal['changes']:
        path = item['path']
        parts = Path(path).parts
        if len(parts) >= 4 and '/'.join(parts[:2]) in SKILL_TARGETS:
            changed.setdefault(parts[2], []).append(path)
    affected = []
    for name, paths in changed.items():
        before = _effective_skill(project, name, old, trees[old], stacks)
        after = _effective_skill(project, name, new, trees[new], stacks)
        # Compare effective selection by name: adding/removing a higher-priority copy can
        # remove/restore an oracle even though the old/new selected path itself did not change.
        if before != after and (before['roles'] or after['roles']):
            affected.extend(paths)
    return sorted(set(affected))


def _effective_skill(project: Path, name: str, commit: str, tree: set[str], stacks: list[str]) -> dict:
    header_path = next((f'{target}/{name}/SKILL.md' for target in SKILL_TARGETS
                        if f'{target}/{name}/SKILL.md' in tree), None)
    packaged = SKILLS_ROOT / name / 'SKILL.md'
    if header_path is None and not packaged.is_file():
        return {'roles': []}
    if header_path is None:
        body, source = _skill_text(packaged.parent), 'packaged/' + name
    else:
        body, source = _committed_body(project, commit, header_path, tree), header_path
    meta = _frontmatter(body)
    if 'roles' not in meta and packaged.is_file():
        meta['roles'] = _frontmatter(packaged.read_text(encoding='utf-8')).get('roles', '[]')
    skill_stacks = _list_field(meta.get('stacks'))
    roles = ORACLE_ROLES.intersection(_list_field(meta.get('roles')))
    if skill_stacks and not set(stacks).intersection(skill_stacks):
        roles = set()
    return {'source': source, 'roles': sorted(roles),
            'sha256': hashlib.sha256(body.encode('utf-8')).hexdigest()}


def _committed_body(project: Path, commit: str, header: str, tree: set[str]) -> str:
    """Match the delivered skill body, including direct Markdown reference files."""
    parts = [_git(project, 'show', f'{commit}:{header}')]
    reference_root = header.rsplit('/', 1)[0] + '/references'
    for path in sorted(tree):
        if path.rsplit('/', 1)[0] == reference_root and path.endswith('.md'):
            name = path.rsplit('/', 1)[1]
            parts.append(f'\n\n<!-- references/{name} -->\n' + _git(project, 'show', f'{commit}:{path}'))
    return ''.join(parts)
