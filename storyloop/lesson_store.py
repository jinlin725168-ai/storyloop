"""Read and evaluate lessons from an immutable Git commit."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from .baseline import _git
from .lessons import LESSONS_ROOT, parse_lesson, validate_lessons
from .models import StoryLoopError, identifier
from .storage import load_json


def _invalid(reason: str) -> StoryLoopError:
    return StoryLoopError(f"经验快照无效：{reason}", code="LESSON_INVALID")


def _resolve_commit(project: Path, commit: str) -> str:
    try:
        if not isinstance(commit, str) or not commit:
            raise _invalid("提交引用必须是非空字符串")
        resolved = _git(
            project, "rev-parse", "--verify", "--end-of-options", f"{commit}^{{commit}}",
        ).strip()
        if len(resolved) != 40 or any(character not in "0123456789abcdef" for character in resolved):
            raise _invalid("Git 未返回完整提交 ID")
        return resolved
    except StoryLoopError as exc:
        raise _invalid("提交不存在或不是提交对象") from exc


def _tree_record(project: Path, commit: str, path: str) -> tuple[str, str, str] | None:
    output = _git(project, "ls-tree", commit, "--", path).strip()
    if not output:
        return None
    records = output.splitlines()
    if len(records) != 1 or "\t" not in records[0]:
        raise _invalid(f"Git 树条目异常：{path}")
    header, actual = records[0].split("\t", 1)
    parts = header.split()
    if len(parts) != 3 or actual != path:
        raise _invalid(f"Git 树条目异常：{path}")
    return parts[0], parts[1], parts[2]


def lessons_digest(project: Path, commit: str) -> str | None:
    """Return the frozen lesson tree ID, or None when that tree is absent."""
    resolved = _resolve_commit(Path(project).resolve(), commit)
    record = _tree_record(Path(project).resolve(), resolved, LESSONS_ROOT)
    if record is None:
        return None
    if record[0] != "040000" or record[1] != "tree":
        raise _invalid(f"{LESSONS_ROOT} 必须是目录")
    return record[2]


def _lesson_blobs(project: Path, commit: str) -> list[tuple[str, str]]:
    output = _git(project, "ls-tree", "-r", "-z", commit, "--", LESSONS_ROOT)
    result = []
    for raw in filter(None, output.split("\0")):
        if "\t" not in raw:
            raise _invalid("Git 经验条目格式异常")
        header, path = raw.split("\t", 1)
        parts = header.split()
        pure = PurePosixPath(path)
        if (len(parts) != 3 or parts[0] not in {"100644", "100755"}
                or parts[1] != "blob" or pure.parent.as_posix() != LESSONS_ROOT
                or pure.suffix != ".md"):
            raise _invalid(f"经验目录包含不安全或非文档条目：{path}")
        result.append((path, parts[2]))
    return sorted(result)


def _landing_exists(project: Path, commit: str, path: str) -> bool:
    record = _tree_record(project, commit, path)
    return bool(record and record[0] in {"100644", "100755"} and record[1] == "blob")


def _validate_landings_at(project: Path, commit: str, entries: list[dict]) -> None:
    for entry in entries:
        for landing in entry["landings"]:
            if landing["kind"] == "foundation":
                path = landing["path"]
            elif landing["kind"] == "principles":
                path = "openspec/principles.json"
            else:
                continue  # check commands are local config and have no historical Git object
            if not _landing_exists(project, commit, path):
                raise _invalid(f"{entry['id']} 的落点在冻结提交中不存在：{path}")


def load_lessons_at(project: Path, commit: str) -> list[dict]:
    """Load and validate all lesson documents from one frozen commit."""
    project = Path(project).resolve()
    resolved = _resolve_commit(project, commit)
    if lessons_digest(project, resolved) is None:
        return []
    try:
        entries = [parse_lesson(_git(project, "show", blob), path)
                   for path, blob in _lesson_blobs(project, resolved)]
        from .project_agents import ROLES  # local import avoids the Task 4 import cycle
        validate_lessons(entries, set(ROLES))
        _validate_landings_at(project, resolved, entries)
        return entries
    except StoryLoopError as exc:
        if exc.code == "LESSON_INVALID":
            raise
        raise _invalid(str(exc)) from exc


def lesson_source_commit(project: Path, change_id: str | None) -> str:
    """Choose the frozen source for a run, with compatibility for older states."""
    project = Path(project).resolve()
    if change_id:
        change_id = identifier(change_id, "change id")
        path = project / ".storyloop/state/runs" / f"{change_id}.json"
        if path.is_file():
            state = load_json(path)
            if isinstance(state.get("contract_commit"), str) and state["contract_commit"]:
                return state["contract_commit"]
            if isinstance(state.get("source_head"), str) and state["source_head"]:
                return state["source_head"]
    return _git(project, "rev-parse", "HEAD").strip()


def stale_ids(project: Path, entries: list[dict], commit: str) -> set[str]:
    """Compute active lessons whose scope changed after their baseline commit."""
    project = Path(project).resolve()
    resolved = _resolve_commit(project, commit)
    ancestors = set(_git(project, "rev-list", resolved).splitlines())
    stale = set()
    for entry in entries:
        if entry["status"] != "active":
            continue
        try:
            baseline = _resolve_commit(project, entry["baseline_commit"])
        except StoryLoopError:
            stale.add(entry["id"])
            continue
        if baseline not in ancestors:
            stale.add(entry["id"])
            continue
        roots = [scope.removesuffix("/**") for scope in entry["scope"] if scope != "**"]
        arguments = ["log", "--format=%H", f"{baseline}..{resolved}"]
        if roots:
            arguments.extend(["--", *roots])
        if _git(project, *arguments).strip():
            stale.add(entry["id"])
    return stale


def effective_status(entry: dict, stale: set[str]) -> str:
    """Return the computed status without mutating the persisted lesson."""
    if entry["status"] == "active" and entry["id"] in stale:
        return "needs-review"
    return entry["status"]
