"""Skills packaged with the engine, installed into any project for Claude Code and Codex.

A skill is a directory with `SKILL.md` (frontmatter: name, version, description) and optional
`references/`. The packaged copy under `storyloop/skills/` is the source of truth and is
maintained here; `storyloop skills <project>` copies every skill into the locations each agent
tool discovers (`.claude/skills/<name>/` for Claude Code, `.codex/skills/<name>/` for Codex).
Those copies are ordinary tracked files, so on an initialized project they enter the protected
foundation through a BASE proposal like any other base change.

The engine reads the same files when it runs a role that is defined by a skill (the `survey`
role), preferring the project's installed copy so local amendments win over the packaged one.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re

from .models import require

SKILLS_ROOT = Path(__file__).resolve().parent / "skills"
SKILL_TARGETS = (".claude/skills", ".codex/skills")
MAX_SKILLS = 6
MAX_SKILL_BYTES = 200_000


def select_skills(project: Path, role: str, stacks: list[str]) -> list[str]:
    """Select bounded role/stack-compatible skills, with project copies taking precedence."""
    return [snapshot['name'] for _, snapshot in selected_snapshots(project, role, stacks)]


def selected_snapshots(project: Path, role: str, stacks: list[str]) -> list[tuple[str, dict]]:
    """Capture selection metadata, delivered bodies and fingerprints in a single read."""
    require(isinstance(stacks, list) and all(isinstance(s, str) for s in stacks), 'Invalid stacks')
    project = Path(project).resolve()
    names = set()
    for base in [*(project / target for target in SKILL_TARGETS), SKILLS_ROOT]:
        if base.exists():
            _safe_skill_path(base, base.parent)
        names.update(path.parent.name for path in base.glob('*/SKILL.md'))
    selected, total = [], 0
    for name in sorted(names):
        root = _skill_root(project, name)
        header = _read_skill_file(root / 'SKILL.md', root)
        meta = _frontmatter(header)
        roles = meta.get('roles')
        packaged = SKILLS_ROOT / name / 'SKILL.md'
        if roles is None and root != SKILLS_ROOT / name and packaged.is_file():
            roles = _frontmatter(_read_skill_file(packaged, SKILLS_ROOT / name)).get('roles')
        if role not in _list_field(roles) or (_list_field(meta.get('stacks'))
                and not set(stacks).intersection(_list_field(meta.get('stacks')))):
            continue
        body = _skill_text(root, header)
        total += len(body.encode('utf-8'))
        require(len(selected) < MAX_SKILLS and total <= MAX_SKILL_BYTES, 'Selected skills exceed limits')
        selected.append((body, _snapshot(project, root, body)))
    return selected


def _list_field(value: str | None) -> list[str]:
    if value is None:
        return []
    require(value.startswith('[') and value.endswith(']'), 'Skill roles/stacks must be lists')
    return [part.strip().strip('"\'') for part in value[1:-1].split(',') if part.strip()]


def used_skills(project: Path, names: list[str]) -> dict[str, str]:
    """name -> version of the copy `skill_text` would read (project copy first, packaged fallback)."""
    project = Path(project).resolve()
    versions = {}
    for name in names:
        root = _skill_root(project, name)
        versions[name] = _frontmatter((root / "SKILL.md").read_text(encoding="utf-8")).get("version", "0")
    return versions
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def packaged_skills() -> dict[str, dict]:
    """name -> {version, description, root, files} for every skill shipped with the engine."""
    skills: dict[str, dict] = {}
    for skill_file in sorted(SKILLS_ROOT.glob("*/SKILL.md")):
        meta = _frontmatter(skill_file.read_text(encoding="utf-8"))
        root = skill_file.parent
        files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
        skills[root.name] = {"version": meta.get("version", "0"), "description": meta.get("description", ""),
                             "root": root, "files": files}
    return skills


def install_skills(project: Path) -> dict:
    """Copy every packaged skill into the project's Claude and Codex skill directories."""
    project = Path(project).resolve()
    written: list[str] = []
    for name, skill in packaged_skills().items():
        for target in SKILL_TARGETS:
            for relative in skill["files"]:
                source = skill["root"] / relative
                destination = project / target / name / relative
                content = source.read_bytes()
                if destination.is_file() and destination.read_bytes() == content:
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
                written.append(destination.relative_to(project).as_posix())
    result = {"status": "SKILLS_INSTALLED", "written": sorted(written), "skills": skill_status(project)}
    if written and (project / ".storyloop/baseline.json").is_file():
        from .base_upgrade import propose_base_change  # skills are base files on an initialized project

        proposal = propose_base_change(project, sorted(written), "install or refresh packaged skills")
        result.update({"proposal": proposal["proposal"], "change_id": proposal["change_id"],
                       "next": proposal["next"]})
    elif written:
        result["next"] = "commit these files, then run storyloop init"
    return result


def skill_status(project: Path) -> dict[str, dict]:
    """Packaged version versus the version installed for each agent tool."""
    project = Path(project).resolve()
    status: dict[str, dict] = {}
    for name, skill in packaged_skills().items():
        installed = {}
        for target in SKILL_TARGETS:
            path = project / target / name / "SKILL.md"
            if path.is_file():
                installed[target] = _frontmatter(path.read_text(encoding="utf-8")).get("version", "0")
        status[name] = {"packaged": skill["version"], "installed": installed}
    return status


def skill_text(project: Path, name: str) -> str:
    """The skill's SKILL.md plus references, project copy first, packaged copy as fallback."""
    root = _skill_root(project, name)
    return _skill_text(root)


def instruction_skill_snapshot(project: Path, name: str) -> tuple[str, dict]:
    """Return the exact delivered skill text and its immutable call provenance."""
    project = Path(project).resolve()
    root = _skill_root(project, name)
    text = _skill_text(root)
    return text, _snapshot(project, root, text)


def _snapshot(project: Path, root: Path, text: str) -> dict:
    skill_file = root / "SKILL.md"
    try:
        path = skill_file.relative_to(project).as_posix()
    except ValueError:
        path = skill_file.relative_to(SKILLS_ROOT.parent.parent).as_posix()
    return {
        "name": root.name,
        "path": path,
        "version": _frontmatter(text).get("version", "0"),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def _skill_root(project: Path, name: str) -> Path:
    project = Path(project).resolve()
    require(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', name), 'Invalid skill name')
    roots = [project / target / name for target in SKILL_TARGETS] + [SKILLS_ROOT / name]
    for root in roots:
        if (root / 'SKILL.md').exists() or (root / 'SKILL.md').is_symlink():
            _safe_skill_path(root / 'SKILL.md', project if root != SKILLS_ROOT / name else SKILLS_ROOT)
            return root
    require(False, f'Unknown skill {name}')


def _safe_skill_path(path: Path, base: Path) -> None:
    from .path_security import safe_target
    safe_target(base, path.relative_to(base).as_posix(), 'skill path', target_directory=path.is_dir())


def _read_skill_file(path: Path, root: Path) -> str:
    _safe_skill_path(path, root)
    require(path.stat().st_size <= MAX_SKILL_BYTES, 'Selected skills exceed size limit')
    return path.read_text(encoding='utf-8')


def _skill_text(root: Path, header: str | None = None) -> str:
    parts = [header if header is not None else _read_skill_file(root / 'SKILL.md', root)]
    for reference in sorted((root / 'references').glob('*.md')) if (root / 'references').is_dir() else []:
        parts.append(f'\n\n<!-- references/{reference.name} -->\n' + _read_skill_file(reference, root))
        require(sum(len(part.encode('utf-8')) for part in parts) <= MAX_SKILL_BYTES,
                'Selected skills exceed size limit')
    return ''.join(parts)


def _frontmatter(text: str) -> dict[str, str]:
    match = FRONTMATTER.match(text)
    if not match:
        return {}
    meta: dict[str, str] = {}
    block = None
    for line in match.group(1).splitlines():
        if block and line.lstrip().startswith('- '):
            value = line.lstrip()[2:].strip()
            meta[block] = meta[block][:-1] + (', ' if meta[block] != '[]' else '') + value + ']'
            continue
        key, separator, value = line.partition(":")
        if separator and not line.startswith((" ", "-")):
            block = key.strip() if key.strip() in {'roles', 'stacks'} and not value.strip() else None
            meta[key.strip()] = '[]' if block else value.strip()
    return meta


def role_skill_versions(project: Path, stacks: list[str]) -> dict[str, str]:
    """Summarize configured role skill versions; per-call snapshots remain authoritative."""
    return {snapshot['name']: snapshot['version']
            for role in ('product', 'acceptance', 'review', 'implement', 'characterize', 'design', 'survey')
            for _, snapshot in selected_snapshots(project, role, stacks)}
