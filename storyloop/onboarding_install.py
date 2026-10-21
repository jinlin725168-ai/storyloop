"""Stage complete drafts, then install exclusively with ownership-aware rollback."""
import ast
import json
from pathlib import Path
import tempfile

from .initialization_transaction import WriteJournal
from .models import StoryLoopError, require
from .path_security import safe_target


def install(project: Path, artifacts: dict[str, bytes]) -> None:
    """Never overwrite targets; failure preserves independently modified installed files."""
    targets = [safe_target(project, relative, "scaffold target") for relative in artifacts]
    for path in targets:
        require(not path.exists(), f"Scaffold target already exists: {path.relative_to(project)}")
    journal = WriteJournal(targets)
    with tempfile.TemporaryDirectory(prefix="storyloop-scaffold-") as folder:
        staged = _stage(Path(folder), artifacts)
        try:
            for relative, source in staged.items():
                target = safe_target(project, relative, "scaffold target")
                content = source.read_bytes()
                journal.observe(target, content)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as handle:
                    handle.write(content)
        except BaseException as error:
            conflicts = journal.rollback()
            if conflicts:
                raise StoryLoopError("Scaffold failed; concurrent edits preserved: "
                                     + ", ".join(conflicts)) from error
            raise


def _stage(root: Path, artifacts: dict[str, bytes]) -> dict[str, Path]:
    result = {}
    for relative, content in artifacts.items():
        if relative.endswith(".py"):
            ast.parse(content)
        elif relative.endswith(".json"):
            require(isinstance(json.loads(content), dict), "Invalid rendered JSON")
        target = safe_target(root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        result[relative] = target
    return result
