"""Durable state, approval fingerprints and portable exclusive runner locks."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from .models import StoryLoopError
from .path_security import safe_absolute_target

IGNORED = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", "target", ".mypy_cache"}


def load_json(path: Path) -> dict:
    """Read a UTF-8 JSON object, reporting a safe filename on errors."""
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise StoryLoopError(f"Cannot read valid JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise StoryLoopError(f"Expected object: {path.name}")
    return value


def write_json(path: Path, value: dict, max_bytes: int | None = None) -> None:
    """Atomically replace a bounded UTF-8 JSON document after fsync."""
    content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if max_bytes is not None:
        if type(max_bytes) is not int or max_bytes <= 0:
            raise StoryLoopError("Invalid JSON document size limit")
        if len(content.encode("utf-8")) > max_bytes:
            raise StoryLoopError("JSON document exceeds size limit")
    write_bytes_atomic(path, content.encode("utf-8"))


def write_text_atomic(path: Path, content: str) -> None:
    """Atomically write UTF-8 text through an unpredictable exclusive temp file."""
    write_bytes_atomic(path, content.encode("utf-8"))


def write_bytes_atomic(path: Path, content: bytes) -> None:
    """Atomically write bytes without following a pre-created temporary link."""
    path = Path(path).absolute()
    safe_absolute_target(path, "controlled write path")
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_absolute_target(path, "controlled write path")
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

def initialize(project: Path, goal: str = "") -> None:
    """Create a new instance without overwriting any existing contents."""
    project = project.resolve()
    if project.exists() and any(project.iterdir()):
        raise StoryLoopError("Initialization requires an empty or new directory")
    for folder in ("workspace", "qa", ".storyloop"):
        (project / folder).mkdir(parents=True, exist_ok=True)
    product = {"version": 1, "goal": goal, "non_goals": [], "questions": [], "stories": []}
    config = {"version": 1, "adapter": "codex", "agent_command": [], "max_attempts": 3,
              "max_seconds": 1800, "agent_timeout_seconds": 300, "check_timeout_seconds": 60,
              "repeated_failure_limit": 2}
    write_json(project / "product.json", product)
    write_json(project / "checks.json", {"version": 1, "checks": []})
    write_json(project / "config.json", config)
    write_json(project / ".storyloop" / "state.json", {"version": 1, "stories": {}})
    (project / "brief.md").write_text(goal + "\n", encoding="utf-8")


def safe_files(root: Path, ignored: set | None = None) -> list[Path]:
    """Enumerate controlled files without following symlinks or reading secrets."""
    if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
        raise StoryLoopError("Symlinks are not allowed in controlled inputs")
    if root.is_file():
        return [root]
    result = []
    if not root.exists():
        return result
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = sorted(n for n in names if n not in (ignored or set()))
        for name in [*names, *files]:
            item = Path(directory) / name
            if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
                raise StoryLoopError("Links are not allowed in controlled inputs")
        for name in sorted(files):
            if name.startswith(".env") or name.endswith((".key", ".pem")) or name in {"credentials.json", "secrets.yaml"}:
                raise StoryLoopError("Sensitive files must stay outside the controlled project inputs")
            if not ignored or not name.endswith((".pyc", ".pyo")):
                result.append(Path(directory) / name)
    return result


def digest_tree(paths: list[Path], ignored: set | None = None) -> str:
    """Fingerprint file contents and relative names in deterministic order."""
    digest = hashlib.sha256()
    for root in paths:
        digest.update(root.name.encode())
        for path in safe_files(root, ignored):
            relative = path.name if root.is_file() else path.relative_to(root).as_posix()
            digest.update(relative.encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def approval_digest(project: Path) -> str:
    return digest_tree([project / p for p in ("product.json", "checks.json", "config.json", "qa")])


def workspace_digest(project: Path) -> str:
    return digest_tree([project / "workspace"], IGNORED)


@contextmanager
def locked(project: Path) -> Iterator[None]:
    """OS-backed process lock; the kernel releases it on crashes."""
    with locked_file(project / ".storyloop" / "run.lock"):
        yield


@contextmanager
def locked_file(path: Path) -> Iterator[Path]:
    """Acquire a nonblocking OS lock at an explicit control-plane path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0)
    if path.stat().st_size == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    acquired = False
    try:
        _lock_file(handle)
        acquired = True
        yield path
    finally:
        if acquired:
            _unlock_file(handle)
        handle.close()

def _lock_file(handle) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise StoryLoopError("Another operation holds the project lock") from exc


def _unlock_file(handle) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
