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
# Packaged skills each engine role reads; the text is embedded in that role's instructions.
ROLE_SKILLS: dict[str, tuple[str, ...]] = {
    "survey": ("foundation-survey",),
    "product": ("oracle-craft",),
    "acceptance": ("oracle-craft",),
    "characterize": ("oracle-craft",),
    "review": ("oracle-craft",),
}


def used_skills(project: Path, names: list[str]) -> dict[str, str]:
    """name -> version of the copy `skill_text` would read (project copy first, packaged fallback)."""
    project = Path(project).resolve()
    versions = {}
    for name in names:
        roots = [project / target / name for target in SKILL_TARGETS] + [SKILLS_ROOT / name]
        root = next(r for r in roots if (r / "SKILL.md").is_file())
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
    skill_file = root / "SKILL.md"
    try:
        path = skill_file.relative_to(project).as_posix()
    except ValueError:
        path = skill_file.relative_to(SKILLS_ROOT.parent.parent).as_posix()
    return text, {
        "name": name,
        "path": path,
        "version": _frontmatter(text).get("version", "0"),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def _skill_root(project: Path, name: str) -> Path:
    project = Path(project).resolve()
    require(name in packaged_skills(), f"Unknown skill {name}")
    roots = [project / target / name for target in SKILL_TARGETS] + [SKILLS_ROOT / name]
    return next(root for root in roots if (root / "SKILL.md").is_file())


def _skill_text(root: Path) -> str:
    parts = [(root / "SKILL.md").read_text(encoding="utf-8")]
    for reference in sorted((root / "references").glob("*.md")) if (root / "references").is_dir() else []:
        parts.append(f"\n\n<!-- references/{reference.name} -->\n" + reference.read_text(encoding="utf-8"))
    return "".join(parts)


def _frontmatter(text: str) -> dict[str, str]:
    match = FRONTMATTER.match(text)
    if not match:
        return {}
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if separator and not line.startswith((" ", "-")):
            meta[key.strip()] = value.strip()
    return meta
