"""Boundary detection for trusted workspaces after all writer processes have stopped."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .models import StoryLoopError, require
from .storage import IGNORED


_CONTROL_IGNORED = {name.casefold() for name in IGNORED | {".storyloop"}}
_READONLY_IGNORED = {name.casefold() for name in IGNORED if name.casefold() != ".git"}
_SENSITIVE_FILES = {"credentials.json", "secrets.yaml"}
_CHUNK_SIZE = 1024 * 1024
_DEFAULT_MAX_FILES = 100_000


@dataclass(frozen=True)
class FileFingerprint:
    """Metadata sufficient to compare files without retaining their contents."""

    size: int
    sha256: str
    kind: str = "file"
    mode: int = 0
    attributes: int = 0
    mtime_ns: int = 0
    uid: int = 0
    gid: int = 0


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """An immutable, portable view of a project tree."""

    root: Path
    entries: tuple[tuple[str, FileFingerprint], ...]


@dataclass(frozen=True)
class FileChange:
    """A content-free description safe to persist as boundary evidence."""

    path: str
    kind: str


def capture_snapshot(root: Path, max_files: int = _DEFAULT_MAX_FILES,
                     include_control: bool = False,
                     include_ignored: bool = False) -> WorkspaceSnapshot:
    """Capture twice; the caller must first stop every workspace writer."""
    root = Path(root)
    require(type(max_files) is int and max_files > 0, "Invalid snapshot file limit")
    require(type(include_control) is bool, "Invalid include_control option")
    require(type(include_ignored) is bool, "Invalid include_ignored option")
    require(not include_ignored or include_control,
            "Full snapshots must include control paths")
    require(root.exists() and root.is_dir(), "Snapshot root must be an existing directory")
    require(not _is_link(root), "Links are not allowed in snapshot roots")
    resolved = root.resolve()
    if include_control:
        _reject_linked_git_control(resolved)
    ignored = set() if include_ignored else (
        _READONLY_IGNORED if include_control else _CONTROL_IGNORED
    )
    allowed = _sensitive_allow(resolved)
    scratch = _scratch_skip(resolved) if include_ignored else frozenset()
    first = _scan_once(resolved, max_files, ignored, include_control, allowed, scratch)
    second = _scan_once(resolved, max_files, ignored, include_control, allowed, scratch)
    require(first == second, "Workspace changed while capturing snapshot")
    return WorkspaceSnapshot(resolved, first)


def _reject_linked_git_control(root: Path) -> None:
    git_control = root / ".git"
    if not git_control.exists():
        return
    require(not _is_link(git_control), "Links are not allowed in Git control paths")
    if git_control.is_dir():
        return
    require(git_control.is_file(), "Unsupported Git control path")
    try:
        descriptor = _open_descriptor(git_control)
        with os.fdopen(descriptor, "rb") as handle:
            require(os.fstat(handle.fileno()).st_size <= 4096,
                    "Unsupported Git control file")
            content = handle.read(4097)
    except OSError:
        raise StoryLoopError("Cannot inspect Git control path") from None
    require(not content.lstrip().lower().startswith(b"gitdir:"),
            "Linked Git worktrees are not supported by read-only Judge snapshots")


def _scan_once(root: Path, max_files: int, ignored: set[str],
               track_directories: bool,
               allowed: frozenset[str] = frozenset(),
               scratch: frozenset[str] = frozenset()) -> tuple[tuple[str, FileFingerprint], ...]:
    entries: list[tuple[str, FileFingerprint]] = []
    for directory, names, files in os.walk(root, followlinks=False):
        current = Path(directory)
        require(not _is_link(current), "Links are not allowed in project snapshots")
        if scratch:
            base = "" if current == root else current.relative_to(root).as_posix() + "/"
            names[:] = [name for name in names
                        if name not in scratch and base + name not in scratch]
        _reject_links(current, [*names, *files])
        names[:] = sorted(name for name in names if name.casefold() not in ignored)
        if track_directories:
            require(len(entries) < max_files, "Snapshot file limit exceeded")
            relative = "./" if current == root else current.relative_to(root).as_posix() + "/"
            entries.append((relative, _directory_fingerprint(current)))
        for name in sorted(files):
            path = current / name
            if path.relative_to(root).as_posix() == ".storyloop/run.lock":
                continue
            if path.relative_to(root).as_posix() not in allowed:
                _reject_sensitive(path)
            require(path.is_file(), "Unsupported filesystem entry in project")
            require(len(entries) < max_files, "Snapshot file limit exceeded")
            relative = path.relative_to(root).as_posix()
            entries.append((relative, _fingerprint(path)))
    entries.sort(key=lambda item: item[0])
    return tuple(entries)


def snapshot_changes(before: WorkspaceSnapshot, after: WorkspaceSnapshot) -> list[FileChange]:
    """Return stable file-system changes for a writer that is audited separately."""
    require(before.root == after.root, "Snapshots must belong to the same project root")
    previous, current = dict(before.entries), dict(after.entries)
    return [item for item in _changes(before, after)
            if not _directory_mtime_only(item.path, previous, current)]


def _directory_mtime_only(path: str, before: dict, after: dict) -> bool:
    left, right = before.get(path), after.get(path)
    if not left or not right:
        return False
    if left.kind != "directory" or right.kind != "directory":
        return False
    fields = ("kind", "size", "sha256", "mode", "attributes", "uid", "gid")
    return all(getattr(left, name) == getattr(right, name) for name in fields)


def _changes(before: WorkspaceSnapshot, after: WorkspaceSnapshot) -> list[FileChange]:
    previous, current = dict(before.entries), dict(after.entries)
    changes: list[FileChange] = []
    for path in sorted(previous.keys() | current.keys()):
        if path not in previous:
            changes.append(FileChange(path, "added"))
        elif path not in current:
            changes.append(FileChange(path, "deleted"))
        elif previous[path] != current[path]:
            changes.append(FileChange(path, "modified"))
    return changes


def _reject_links(directory: Path, names: list[str]) -> None:
    for name in names:
        require(not _is_link(directory / name), "Links are not allowed in project snapshots")


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise StoryLoopError("Workspace changed while capturing snapshot") from exc
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if getattr(metadata, "st_file_attributes", 0) & reparse:
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _sensitive_allow(root: Path) -> frozenset[str]:
    """Tracked files the project cleared in `candidate_allow` despite sensitive-looking names."""
    config = root / ".storyloop" / "config.json"
    if not config.is_file():
        return frozenset()
    try:
        profile = json.loads(config.read_text(encoding="utf-8")).get("profile", {})
    except (OSError, ValueError):
        return frozenset()
    values = profile.get("candidate_allow", []) if isinstance(profile, dict) else []
    return frozenset(item for item in values if isinstance(item, str))


def _scratch_skip(root: Path) -> frozenset[str]:
    """Declared scratch locations (names or relative paths) are never walked by full snapshots."""
    config = root / ".storyloop" / "config.json"
    if not config.is_file():
        return frozenset()
    try:
        profile = json.loads(config.read_text(encoding="utf-8")).get("profile", {})
    except (OSError, ValueError):
        return frozenset()
    values = profile.get("scratch_paths", []) if isinstance(profile, dict) else []
    return frozenset(item.rstrip("/") for item in values if isinstance(item, str) and item)


def _reject_sensitive(path: Path) -> None:
    name = path.name.casefold()
    require(not name.startswith(".env") and not name.endswith((".key", ".pem"))
            and name not in _SENSITIVE_FILES,
            "Sensitive files must stay outside the controlled project")


def _open_descriptor(path: Path) -> int:
    if os.name == "nt":
        return _open_windows_descriptor(path)
    require(hasattr(os, "O_NOFOLLOW"),
            "Platform cannot safely open files without following links")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | os.O_NOFOLLOW
    return os.open(path, flags)


def _open_windows_descriptor(path: Path) -> int:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel.CreateFileW
    create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                            wintypes.HANDLE]
    create_file.restype = wintypes.HANDLE
    handle = create_file(str(path), 0x80000000, 0x00000007, None, 3,
                         0x00200000, None)
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        error = ctypes.get_last_error()
        raise OSError(error, "Unable to open file without following reparse points")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    try:
        return msvcrt.open_osfhandle(handle, flags)
    except (OSError, ValueError):
        kernel.CloseHandle(handle)
        raise


def _fingerprint(path: Path) -> FileFingerprint:
    try:
        descriptor = _open_descriptor(path)
        with os.fdopen(descriptor, "rb") as handle:
            before = os.fstat(handle.fileno())
            reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            require(not getattr(before, "st_file_attributes", 0) & reparse,
                    "Links are not allowed in project snapshots")
            require(stat.S_ISREG(before.st_mode), "Only regular files can be snapshotted")
            require(before.st_nlink == 1, "Hard links are not allowed in project snapshots")
            digest = hashlib.sha256()
            for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
                digest.update(chunk)
            after = os.fstat(handle.fileno())
            current = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise StoryLoopError("Workspace changed while capturing snapshot") from exc
    require(_stable(before, after) and _stable(after, current) and not _is_link(path),
            "Workspace changed while capturing snapshot")
    return _metadata_fingerprint(after, "file", after.st_size, digest.hexdigest())


def _directory_fingerprint(path: Path) -> FileFingerprint:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise StoryLoopError("Workspace changed while capturing snapshot") from exc
    require(stat.S_ISDIR(metadata.st_mode) and not _is_link(path),
            "Only regular directories can be snapshotted")
    return _metadata_fingerprint(metadata, "directory", 0, "")


def _metadata_fingerprint(metadata: os.stat_result, kind: str, size: int,
                          digest: str) -> FileFingerprint:
    return FileFingerprint(
        size, digest, kind, stat.S_IMODE(metadata.st_mode),
        getattr(metadata, "st_file_attributes", 0), metadata.st_mtime_ns,
        getattr(metadata, "st_uid", 0), getattr(metadata, "st_gid", 0),
    )


def _stable(before: os.stat_result, after: os.stat_result) -> bool:
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_nlink", "st_mode",
              "st_file_attributes", "st_uid", "st_gid")
    return all(getattr(before, field, None) == getattr(after, field, None) for field in fields)
