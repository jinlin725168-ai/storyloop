"""Explicit CLI authorization for changes to skills governing acceptance oracles."""
from contextvars import ContextVar
from pathlib import Path

from .baseline import _git, load_project_config
from .models import require
from .skills import SKILL_TARGETS, SKILLS_ROOT, _frontmatter, _list_field

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
    affected = []
    for item in proposal['changes']:
        path = item['path']
        parts = Path(path).parts
        if len(parts) < 4 or '/'.join(parts[:2]) not in SKILL_TARGETS:
            continue
        name = parts[2]
        for commit in (old, new):
            header_path = next((f'{target}/{name}/SKILL.md' for target in SKILL_TARGETS
                                if f'{target}/{name}/SKILL.md' in trees[commit]), None)
            if header_path is None or not path.startswith(header_path.rsplit('/', 1)[0] + '/'):
                continue
            meta = _frontmatter(_git(project, 'show', f'{commit}:{header_path}'))
            packaged = SKILLS_ROOT / name / 'SKILL.md'
            if 'roles' not in meta and packaged.is_file():
                meta['roles'] = _frontmatter(packaged.read_text(encoding='utf-8')).get('roles', '[]')
            roles, skill_stacks = _list_field(meta.get('roles')), _list_field(meta.get('stacks'))
            if ORACLE_ROLES.intersection(roles) and (not skill_stacks or set(stacks).intersection(skill_stacks)):
                affected.append(path)
                break
    return sorted(set(affected))
