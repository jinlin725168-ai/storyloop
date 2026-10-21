"""Exclusive, ownership-aware initialization journal for explicit control-plane files."""
from contextlib import contextmanager
from pathlib import Path

from .models import StoryLoopError, require
from .path_security import safe_absolute_target, safe_target
from . import storage

CONTROL_FILES = (".git/info/exclude", ".storyloop/config.json", ".storyloop/baseline.json",
                 ".storyloop/state/system-smoke.json", ".storyloop/state/adapters.json")


class WriteJournal:
    """Remember expected bytes before writing; rollback never replaces a concurrent edit."""
    def __init__(self, paths: list[Path]):
        self.before = {path: _read(path) for path in paths}
        self.written = {}
        self.directories = set()

    def observe(self, path: Path, content: bytes) -> None:
        path = Path(path).absolute()
        require(path in self.before, "Initialization attempted an undeclared control write")
        expected = self.written.get(path, self.before[path])
        require(_read(path) == expected, "Concurrent control edit detected before initialization write")
        parent = path.parent
        while not parent.exists():
            self.directories.add(parent)
            parent = parent.parent
        self.written[path] = content

    def rollback(self) -> list[str]:
        conflicts = []
        for path, content in reversed(list(self.written.items())):
            try:
                current = _read(path)
                if current == self.before[path]:
                    continue
                if current != content:
                    conflicts.append(path.name)
                    continue
                if self.before[path] is None:
                    path.unlink()
                else:
                    storage.write_bytes_atomic(path, self.before[path])
            except (OSError, StoryLoopError):
                conflicts.append(path.name)
        for directory in sorted(self.directories, key=lambda p: len(p.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass  # Keep pre-existing or independently created contents.
        return conflicts


def _read(path: Path) -> bytes | None:
    safe_absolute_target(path)
    return path.read_bytes() if path.exists() else None


@contextmanager
def initialization_transaction(project: Path):
    """Serialize initialization and restore only bytes still owned by this attempt."""
    lock = safe_target(project, ".git/storyloop-init.lock", "initialization lock")
    with storage.locked_file(lock):
        journal = WriteJournal([safe_target(project, relative) for relative in CONTROL_FILES])
        token = storage._write_observer.set(journal.observe)
        try:
            yield
        except BaseException as error:
            storage._write_observer.reset(token)
            token = None
            conflicts = journal.rollback()
            if conflicts:
                raise StoryLoopError("Initialization failed; concurrent edits preserved: "
                                     + ", ".join(conflicts)) from error
            raise
        finally:
            if token is not None:
                storage._write_observer.reset(token)
