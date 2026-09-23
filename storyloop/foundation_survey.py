"""The foundation knowledge base: a protected survey of what the base is and how it is extended.

A `survey` agent explores the repository read-only (README, build files, code, APIs, tests,
generator templates, acceptance tooling) and returns a fixed set of Markdown documents. The
engine writes them under `openspec/foundation/` and opens a BASE proposal, so they enter the
protected foundation through the normal base-update gate. From then on every role receives
`payload.foundation` (baseline version + document paths) and is told to read them first.

The knowledge base follows the baseline, not the people: re-run `storyloop survey` after an
upstream sync or any base change that alters modules, mechanisms or extension points.
"""

from __future__ import annotations

from pathlib import Path

from .base_upgrade import propose_base_change
from .baseline import load_baseline
from .models import require, text
from .path_security import safe_target
from .processutil import redact

FOUNDATION_ROOT = "openspec/foundation"
REQUIRED_DOCUMENTS = (
    "README.md",           # index, baseline version, how to use the knowledge base
    "modules.md",          # module composition and dependency direction
    "architecture.md",     # architecture and runtime mechanics
    "extension.md",        # how a new feature is added (files, templates, menus, permissions)
    "api.md",              # existing API surface (routes, permission strings)
    "capabilities.md",     # what the base already does (upstream + living specs)
    "building-blocks.md",  # reusable services, utilities, composables, acceptance clients
    "testing.md",          # existing tests and acceptance infrastructure, how to run them
)
MAX_DOCUMENTS = 40
MAX_DOCUMENT_BYTES = 512_000


def survey_foundation(project: Path, reason: str) -> dict:
    """Run the survey role, install its documents and open the BASE proposal that protects them."""
    from .project_agents import invoke_project_agent  # local import: project_agents imports this module

    project = Path(project).resolve()
    reason = text(reason, "survey reason")
    baseline = load_baseline(project)
    payload = {"baseline_version": baseline["baseline_version"], "foundation_root": FOUNDATION_ROOT,
               "required_documents": list(REQUIRED_DOCUMENTS), "module_roots": baseline["module_roots"],
               "integration_paths": baseline["integration_paths"], "reason": reason}
    response = invoke_project_agent(project, "survey", payload)
    written = install_foundation_documents(project, response)
    proposal = propose_base_change(project, written, f"foundation survey: {reason}")
    return {"status": "SURVEYED", "paths": written, "proposal": proposal["proposal"],
            "change_id": proposal["change_id"], "next": proposal["next"]}


def install_foundation_documents(project: Path, response: object) -> list[str]:
    """Validate and write the survey's documents; nothing is written unless everything is valid."""
    project = Path(project).resolve()
    documents = response.get("documents") if isinstance(response, dict) else None
    require(isinstance(documents, list) and 0 < len(documents) <= MAX_DOCUMENTS,
            "Survey must return a bounded list of documents")
    planned: list[tuple[Path, str, str]] = []
    for item in documents:
        require(isinstance(item, dict) and isinstance(item.get("path"), str)
                and isinstance(item.get("content"), str), "Each survey document needs a string path and content")
        content = item["content"]
        require(item["path"].endswith(".md"), "Survey documents must be Markdown")
        require(len(content.encode("utf-8")) <= MAX_DOCUMENT_BYTES, "Survey document is too large")
        require(redact(content) == content,
                f"Survey document {item['path']} contains credential-like content; describe, never copy, configuration")
        relative = f"{FOUNDATION_ROOT}/{item['path']}"
        target = safe_target(project, relative, "foundation document path")
        require(target.is_relative_to(project / FOUNDATION_ROOT), "Foundation document escapes its root")
        planned.append((target, target.relative_to(project).as_posix(), content))
    names = {Path(rel).relative_to(FOUNDATION_ROOT).as_posix() for _, rel, _ in planned}
    missing = sorted(set(REQUIRED_DOCUMENTS) - names)
    require(not missing, "Survey is incomplete, missing: " + ", ".join(missing))
    require(len(names) == len(planned), "Survey documents must have distinct paths")
    for target, _, content in planned:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return sorted(rel for _, rel, _ in planned)


def foundation_payload(project: Path) -> dict | None:
    """What every role is handed once a knowledge base exists: baseline version and document paths."""
    project = Path(project).resolve()
    root = project / FOUNDATION_ROOT
    if not (root / "README.md").is_file():
        return None
    documents = sorted(p.relative_to(project).as_posix() for p in root.rglob("*.md") if p.is_file())
    return {"version": load_baseline(project)["baseline_version"], "documents": documents}
