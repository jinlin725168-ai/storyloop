"""Shared Git repository helpers for lesson tests."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import subprocess
import os
import shutil
import stat

from storyloop.lessons import render_lesson


def _git(project: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=project, capture_output=True, text=True, encoding="utf-8",
        check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def sample(**overrides: object) -> dict:
    entry = {
        "id": "L-0001", "summary": "经验", "tier": "normal",
        "scope": ["**"], "roles": ["implement"], "strength": "prose",
        "source": "manual", "baseline_commit": "HEAD", "status": "active",
        "updated_at": date(2026, 10, 19).isoformat(), "supersedes": [],
        "landings": [], "body": "正文\n",
    }
    entry.update(overrides)
    return entry


def git_repo(
    *, lessons: list[dict] | None = None, raw_lessons: dict[str, str] | None = None,
    files: dict[str, str] | None = None,
) -> Path:
    import tempfile

    project = Path(tempfile.mkdtemp()).resolve()
    _git(project, "init", "-q")
    _git(project, "config", "user.email", "storyloop@example.invalid")
    _git(project, "config", "user.name", "StoryLoop Test")
    (project / ".git/info/exclude").write_text(".storyloop/\n", encoding="utf-8")
    for relative, content in (files or {"README.md": "repo\n"}).items():
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    for entry in lessons or []:
        write_lesson(project, entry)
    for filename, content in (raw_lessons or {}).items():
        target = project / "openspec/lessons" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    commit_all(project, "initial")
    return project


def head(project: Path) -> str:
    return _git(project, "rev-parse", "HEAD")


def write_lesson(project: Path, entry: dict) -> None:
    target = project / "openspec/lessons" / f"{entry['id']}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_lesson(entry), encoding="utf-8")


def commit_all(project: Path, message: str) -> str:
    _git(project, "add", ".")
    _git(project, "commit", "-q", "-m", message)
    return head(project)


def commit_file(project: Path, relative: str, content: str) -> str:
    target = project / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return commit_all(project, f"update {relative}")


def initialized_project() -> Path:
    """Create a bootstrapped project with a real passing regression command."""
    import sys
    from storyloop.baseline import bootstrap_project
    project = git_repo()
    bootstrap_project(project, {'test_commands': [[sys.executable, '-c', 'pass']]})
    return project


def cleanup_repo(project: Path) -> None:
    """Remove a test-owned temporary repository, including Windows read-only objects."""
    def retry_readonly(operation, path, error):
        if not isinstance(error[1], PermissionError):
            raise error[1]
        os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
        operation(path)

    shutil.rmtree(project, onerror=retry_readonly)


def git_blob(project: Path, relative: str, commit: str = "HEAD") -> bytes:
    """Read exact committed fixture bytes without worktree newline conversion."""
    return subprocess.run(["git", "show", f"{commit}:{relative}"], cwd=project,
                          capture_output=True, check=True).stdout
