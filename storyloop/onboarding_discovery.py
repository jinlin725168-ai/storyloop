"""Reuse project discovery after rejecting unsafe explicit build markers."""
from pathlib import Path

from .path_security import safe_target
from .project_profile import SKIP_DIRECTORIES, detect_project

MARKERS = ("pom.xml", "package.json", "build.gradle", "build.gradle.kts", "pyproject.toml",
           "setup.py", "setup.cfg", "requirements.txt", "settings.gradle", "settings.gradle.kts",
           "pnpm-workspace.yaml", "pnpm-lock.yaml", "yarn.lock", "mvnw.cmd", "gradlew.bat")


def safe_discovery(project: Path) -> dict:
    """Read build metadata at the root/one level only, without following marker links."""
    prefixes = [""]
    for path in project.iterdir():
        if path.name.startswith(".") or path.name in SKIP_DIRECTORIES:
            continue
        if path.is_dir() and not path.is_symlink():
            safe_target(project, path.name, "detection directory", target_directory=True)
            prefixes.append(path.name + "/")
    for prefix in prefixes:
        for name in MARKERS:
            safe_target(project, prefix + name, "detection marker")
    return detect_project(project)
