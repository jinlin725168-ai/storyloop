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
import re

from .base_upgrade import propose_base_change
from .baseline import _git, load_baseline
from .models import StoryLoopError, require, text
from .path_security import safe_target
from .storage import load_json, write_json

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
LAST_RESPONSE = ".storyloop/state/foundation/last-survey.json"
# Configuration files whose sensitive values must never be copied into the knowledge base.
CONFIG_FILE = re.compile(r"(^|/)(application|bootstrap|config|settings|secrets?)[^/]*\.(ya?ml|properties|toml|ini|json)$"
                         r"|(^|/)\.env[^/]*$", re.I)
SENSITIVE_KEY = ("password", "passwd", "secret", "token", "apikey", "api-key", "api_key", "access-key",
                 "access_key", "accesskey", "secret-key", "secret_key", "secretkey", "private-key",
                 "private_key", "privatekey", "credential", "credentials")
CONFIG_LINE = re.compile(r"^\s*[-\s]*([A-Za-z0-9_.\-\[\]]+)\s*[:=]\s*(.+?)\s*$")
URL_WITH_CREDENTIALS = re.compile(r"[a-z][a-z0-9+.-]*://[^\s/@]+:[^\s/@]+@", re.I)


def survey_foundation(project: Path, reason: str, from_file: Path | None = None) -> dict:
    """Run the survey role (or reuse a saved response), install its documents, open the BASE proposal.

    The raw response is kept at LAST_RESPONSE before validation: a survey costs real agent time,
    so a rejected document is fixed by hand and installed again with `--from`, not re-surveyed.
    """
    from .project_agents import invoke_project_agent  # local import: project_agents imports this module

    project = Path(project).resolve()
    reason = text(reason, "survey reason")
    baseline = load_baseline(project)
    saved = project / LAST_RESPONSE
    if from_file is not None:
        response = load_json(Path(from_file).resolve())
    else:
        payload = {"baseline_version": baseline["baseline_version"], "foundation_root": FOUNDATION_ROOT,
                   "required_documents": list(REQUIRED_DOCUMENTS), "module_roots": baseline["module_roots"],
                   "integration_paths": baseline["integration_paths"], "reason": reason}
        response = invoke_project_agent(project, "survey", payload)
        write_json(saved, response, max_bytes=MAX_DOCUMENTS * MAX_DOCUMENT_BYTES + 100_000)
    try:
        written = install_foundation_documents(project, response)
    except StoryLoopError as error:
        source = Path(from_file).resolve() if from_file is not None else saved
        raise StoryLoopError(f"{error}. The survey response is kept at {source}; fix it and rerun "
                             f"`storyloop survey <project> --from {source}`", code=error.code) from None
    proposal = propose_base_change(project, written, f"foundation survey: {reason}")
    return {"status": "SURVEYED", "paths": written, "proposal": proposal["proposal"],
            "change_id": proposal["change_id"], "next": proposal["next"]}


def install_foundation_documents(project: Path, response: object) -> list[str]:
    """Validate and write the survey's documents; nothing is written unless everything is valid."""
    project = Path(project).resolve()
    documents = response.get("documents") if isinstance(response, dict) else None
    require(isinstance(documents, list) and 0 < len(documents) <= MAX_DOCUMENTS,
            "Survey must return a bounded list of documents")
    secrets = _configured_secret_values(project)
    planned: list[tuple[Path, str, str]] = []
    for item in documents:
        require(isinstance(item, dict) and isinstance(item.get("path"), str)
                and isinstance(item.get("content"), str), "Each survey document needs a string path and content")
        path, content = item["path"], item["content"]
        require(path.endswith(".md"), "Survey documents must be Markdown")
        require(len(content.encode("utf-8")) <= MAX_DOCUMENT_BYTES, "Survey document is too large")
        leaked = URL_WITH_CREDENTIALS.search(content) or any(value in content for value in secrets)
        require(not leaked, f"Survey document {path} copies a configured secret; describe configuration "
                            "by key and location, never by value")
        target = safe_target(project, f"{FOUNDATION_ROOT}/{path}", "foundation document path")
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


def _configured_secret_values(project: Path) -> set[str]:
    """Values of password/secret/token-like keys in tracked configuration files (never logged)."""
    values: set[str] = set()
    tracked = [p for p in _git(project, "ls-files", "-z").split("\0") if p and CONFIG_FILE.search(p)][:200]
    for relative in tracked:
        path = project / relative
        try:
            if path.stat().st_size > 256_000:
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            match = CONFIG_LINE.match(line)
            if not match:
                continue
            key = match.group(1).lower().rsplit(".", 1)[-1].strip("[]")
            if not any(key == name or key.endswith(name) for name in SENSITIVE_KEY):
                continue
            value = match.group(2).strip().strip("'\"").split(" #")[0].strip()
            if len(value) >= 4 and not any(mark in value for mark in ("${", "{{", "<", ">", "*", "[REDACTED]")):
                values.add(value)
    return values


def foundation_payload(project: Path) -> dict | None:
    """What every role is handed once a knowledge base exists: baseline version and document paths."""
    project = Path(project).resolve()
    root = project / FOUNDATION_ROOT
    if not (root / "README.md").is_file():
        return None
    documents = sorted(p.relative_to(project).as_posix() for p in root.rglob("*.md") if p.is_file())
    return {"version": load_baseline(project)["baseline_version"], "documents": documents}
