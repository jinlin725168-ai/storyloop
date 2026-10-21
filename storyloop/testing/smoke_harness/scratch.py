"""Per-repository/run leases, content synchronization, and private dependency copies."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from ...models import require
from ...path_security import safe_absolute_target, safe_target
from ...storage import write_bytes_atomic
from .sources import source_list, source_path


class Scratch:
    """Own one disposable directory; no shared writable builds and no implicit source scan."""
    def __init__(self, project: Path, base: Path | None = None):
        identity = os.path.normcase(str(Path(project).resolve()))
        self.repository_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        parent = Path(base or tempfile.gettempdir()).absolute()
        safe_absolute_target(parent / ".storyloop-path-check")
        require(parent.is_dir(), "Scratch base is unavailable")
        self.root = Path(tempfile.mkdtemp(prefix=f"storyloop-{self.repository_id}-", dir=parent))
        self.mirror = self.root / "mirror"
        self.mirror.mkdir()
        self._lease = json.dumps({"repository": self.repository_id, "run": uuid.uuid4().hex}).encode()
        self._marker = self.root / ".storyloop-lease.json"
        self._marker.write_bytes(self._lease)
        self._owned = {}
        self._closed = False

    def sync(self, project: Path, files: list[str]) -> Path:
        """Copy explicit files by content; remove only owned mirrors whose sources disappeared."""
        self._verify()
        project = Path(project).resolve()
        require(hashlib.sha256(os.path.normcase(str(project)).encode()).hexdigest()[:20]
                == self.repository_id, "Scratch lease belongs to another repository")
        selected = source_list(project, files)
        for relative in selected:
            source = source_path(project, relative)
            require(source.exists() or relative in self._owned, "Declared source is missing")
        for relative in selected:
            source = source_path(project, relative)
            if not source.exists():
                continue
            content = source.read_bytes()
            target = safe_target(self.mirror, relative, "mirror file")
            if not target.exists() or target.read_bytes() != content:
                write_bytes_atomic(target, content)
            self._owned[relative] = hashlib.sha256(content).hexdigest()
        for relative in list(self._owned):
            if not source_path(project, relative).exists():
                self._remove_mirror(relative)
        return self.mirror

    def _remove_mirror(self, relative: str) -> None:
        target = safe_target(self.mirror, relative, "mirror deletion")
        if target.exists():
            require(hashlib.sha256(target.read_bytes()).hexdigest() == self._owned[relative],
                    "Mirror was independently modified; deletion refused")
            target.unlink()
        del self._owned[relative]

    def cache(self, name: str, root: Path, files: list[str]) -> Path:
        """Read explicitly listed dependency archives into a private per-run cache copy."""
        self._verify()
        require(isinstance(name, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", name), "Invalid cache name")
        root = Path(root).absolute()
        safe_absolute_target(root / ".storyloop-path-check")
        require(root.is_dir(), "Declared dependency cache is missing; prewarm it offline")
        selected = source_list(root, files)
        require(bool(selected), "Dependency cache files must be explicit")
        for relative in selected:
            require(relative.endswith((".jar", ".pom", ".whl", ".tgz", ".zip", ".tar.gz")),
                    "Cache may contain dependency archives only, not source or tests")
            require(source_path(root, relative).is_file(), "Declared dependency archive is missing")
        destination = safe_target(self.root, f"dependencies/{name}", target_directory=True)
        destination.mkdir(parents=True, exist_ok=True)
        for relative in selected:
            target = safe_target(destination, relative, "private cache target")
            write_bytes_atomic(target, source_path(root, relative).read_bytes())
        return destination

    def _verify(self) -> None:
        require(not self._closed, "Scratch lease is closed")
        safe_absolute_target(self._marker)
        require(self._marker.is_file() and self._marker.read_bytes() == self._lease,
                "Scratch ownership changed; cleanup refused")

    def close(self) -> None:
        """Delete this lease only after checking ownership and every entry for unsafe links."""
        if self._closed:
            return
        self._verify()
        for current, directories, files in os.walk(self.root, followlinks=False):
            for name in [*directories, *files]:
                path = Path(current) / name
                safe_target(self.root, path.relative_to(self.root).as_posix(),
                            "scratch cleanup", target_directory=path.is_dir())
        self._verify()
        shutil.rmtree(self.root)
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()
