"""Explicit source/cache lists only; credential names are rejected before opening files."""
from pathlib import Path

from ...models import require
from ...path_security import repository_relative, safe_target

PRIVATE = {".git", ".storyloop", ".aws", ".azure", ".ssh", ".gnupg", ".kube",
           ".npmrc", ".pypirc", "credentials.json", "secrets.yaml", "settings.xml"}


def source_path(project: Path, relative: str) -> Path:
    """Validate a single explicit source without recursively discovering other files."""
    normalized = repository_relative(relative, "source file")
    parts = normalized.casefold().split("/")
    require(not any(part in PRIVATE or part.startswith(".env") or
                    part.endswith((".pem", ".key")) or any(word in part for word in
                    ("password", "credential", "secret", "token")) for part in parts),
            "Sensitive files cannot enter a harness mirror")
    path = safe_target(project, normalized, "source file")
    require(not path.exists() or path.is_file(), "Source allowlist must contain files, not directories")
    return path


def source_list(project: Path, values: list[str]) -> list[str]:
    """Use safe_target's canonical root spelling, including Windows 8.3 aliases."""
    require(isinstance(values, list) and len(values) <= 10000, "Explicit bounded source file list required")
    project = Path(project).resolve()
    result = []
    for value in values:
        path = source_path(project, value)
        result.append(path.relative_to(project).as_posix())
    require(len(set(result)) == len(result), "Duplicate source file")
    return result
