"""Read and evaluate lessons from an immutable Git commit."""

from __future__ import annotations

from datetime import date
from pathlib import Path, PurePosixPath
import shutil

from .baseline import GIT_ENVIRONMENT, _git, _git_failure
from .git_command import git_process_policy
from .processutil import run_process
from .lessons import LESSONS_ROOT, parse_lesson, validate_lessons
from .models import StoryLoopError, identifier, require, text
from .path_security import forbidden_path_root
from .project_git import commit_paths, current_branch
from .storage import load_json, locked, write_bytes_atomic, write_text_atomic


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
    output = _git(project, "ls-tree", "-z", commit, "--", path)
    if not output:
        return None
    records = output.rstrip("\0").split("\0")
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


def _is_ancestor(project: Path, baseline: str, commit: str) -> bool:
    """Only a clean Git exit 1 means non-ancestor; diagnostics remain errors."""
    executable = shutil.which("git")
    require(bool(executable), "Git executable is unavailable")
    prefix, environment = git_process_policy(project)
    result = run_process(
        [executable, *prefix, "merge-base", "--is-ancestor", baseline, commit], project, 30,
        environment_allowlist=GIT_ENVIRONMENT, source_environment=environment,
    )
    if (result.get("contained") is not True or result.get("timed_out")
            or result.get("truncated") or result.get("error")):
        raise StoryLoopError("Git process failed safely", code="GIT_BLOCKED")
    # Git can return 1 with an error diagnostic for a missing parent object.
    if result.get("returncode") not in (0, 1) or result.get("stderr", "").strip():
        raise StoryLoopError(_git_failure(result.get("stderr", "")), code="GIT_BLOCKED")
    return result["returncode"] == 0


def stale_ids(project: Path, entries: list[dict], commit: str) -> set[str]:
    """Compute active lessons whose scope changed after their baseline commit."""
    project = Path(project).resolve()
    resolved = _resolve_commit(project, commit)
    stale = set()
    for entry in entries:
        if entry["status"] != "active":
            continue
        try:
            baseline = _resolve_commit(project, entry["baseline_commit"])
        except StoryLoopError:
            stale.add(entry["id"])
            continue
        if not _is_ancestor(project, baseline, resolved):
            stale.add(entry["id"])
            continue
        roots = ([] if "**" in entry["scope"] else
                 [forbidden_path_root(scope) for scope in entry["scope"]])
        arguments = ["log", "--format=%H", f"{baseline}..{resolved}"]
        # Human confirmation changes the lesson itself, not the evidence it describes.
        arguments.extend(["--", *(roots or ["."]), ":(exclude)openspec/lessons/**"])
        if _git(project, *arguments).strip():
            stale.add(entry["id"])
    return stale


def effective_status(entry: dict, stale: set[str]) -> str:
    """Return the computed status without mutating the persisted lesson."""
    if entry["status"] == "active" and entry["id"] in stale:
        return "needs-review"
    return entry["status"]


def _recurrence_count(value: object) -> int:
    recurrences = value.get("recurrences", []) if isinstance(value, dict) else []
    return len(recurrences) if isinstance(recurrences, list) else 0


def _recent_recurrence_rate(project: Path) -> float | None:
    from .telemetry import ledger_entries

    runs = project / ".storyloop/state/runs"
    eligible = []
    for path in sorted(runs.glob("*.json"), reverse=True) if runs.is_dir() else []:
        state = load_json(path)
        entries = ledger_entries(project, path.stem)
        provided = any(isinstance(entry.get("lessons_provided"), list)
                       and bool(entry["lessons_provided"]) for entry in entries)
        if provided:
            eligible.append(state)
            if len(eligible) == 30:
                break
    if not eligible:
        return None
    return sum(bool(state.get("failures")) for state in eligible) / len(eligible)


def list_lessons(project: Path) -> dict:
    """List HEAD lessons with computed freshness and vector-search signals."""
    project = Path(project).resolve()
    commit = _git(project, "rev-parse", "HEAD").strip()
    entries = load_lessons_at(project, commit)
    stale = stale_ids(project, entries, commit)
    state_path = project / ".storyloop/state/lessons.json"
    recurrence_state = load_json(state_path) if state_path.is_file() else {}
    rows = [{
        "id": entry["id"], "summary": entry["summary"], "tier": entry["tier"],
        "strength": entry["strength"], "status": entry["status"],
        "effective_status": effective_status(entry, stale),
        "recurrences": _recurrence_count(recurrence_state.get(entry["id"])),
    } for entry in entries]
    rate = _recent_recurrence_rate(project)
    signal = {"count": len(rows), "recurrence_rate": rate,
              "reevaluate": len(rows) > 200 or (rate is not None and rate > 0.3)}
    return {"lessons": rows, "count": len(rows), "vector_signal": signal}


def require_idle_source(project: Path) -> None:
    """Require a clean non-feature source branch with no active run."""
    branch = current_branch(project)
    require(branch and not branch.startswith("feature/"), "不能在 feature/ 分支修改经验")
    require(not _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
            "修改经验前工作区必须干净")
    runs = project / ".storyloop/state/runs"
    for path in sorted(runs.glob("*.json")) if runs.is_dir() else []:
        if load_json(path).get("status") not in {"PASSED", "SUPERSEDED"}:
            raise StoryLoopError(f"存在进行中的运行：{path.stem}")


def _mutated_entries(entries: list[dict], lesson_id: str, updates: dict) -> tuple[list[dict], dict]:
    lesson_id = text(lesson_id, "lesson id")
    require(any(entry["id"] == lesson_id for entry in entries), f"经验不存在：{lesson_id}")
    from .lessons import render_lesson
    changed = [parse_lesson(render_lesson({**entry, **updates}), f"{lesson_id}.md")
               if entry["id"] == lesson_id else entry for entry in entries]
    from .project_agents import ROLES
    validate_lessons(changed, set(ROLES))
    return changed, next(entry for entry in changed if entry["id"] == lesson_id)


def _commit_lesson(project: Path, entry: dict, message: str, original_head: str) -> dict:
    relative = f"{LESSONS_ROOT}/{entry['id']}.md"
    target = project / relative
    original = target.read_bytes()
    from .lessons import render_lesson
    try:
        write_text_atomic(target, render_lesson(entry))
        return commit_paths(project, [relative], message, details=False)
    except Exception:
        if _git(project, "rev-parse", "HEAD").strip() == original_head:
            write_bytes_atomic(target, original)
            _git(project, "reset", "--", relative)
        raise


def confirm_lesson(project: Path, lesson_id: str) -> dict:
    """Confirm one lesson against current HEAD and commit the human mutation."""
    project = Path(project).resolve()
    with locked(project):
        require_idle_source(project)
        commit = _git(project, "rev-parse", "HEAD").strip()
        entries = load_lessons_at(project, commit)
        current = next((entry for entry in entries if entry["id"] == lesson_id), None)
        require(current is not None, f"经验不存在：{lesson_id}")
        require(current["status"] != "retired", "不能确认 retired 经验")
        _, entry = _mutated_entries(entries, lesson_id, {
            "baseline_commit": commit, "status": "active", "updated_at": date.today().isoformat(),
        })
        return _commit_lesson(
            project, entry, f"chore(lessons): 确认 {entry['id']} 仍然有效", commit,
        )


def retire_lesson(project: Path, lesson_id: str, reason: str) -> dict:
    """Retire one lesson with a dated reason and commit the human mutation."""
    project = Path(project).resolve()
    reason = text(reason, "retirement reason")
    with locked(project):
        require_idle_source(project)
        commit = _git(project, "rev-parse", "HEAD").strip()
        entries = load_lessons_at(project, commit)
        current = next((entry for entry in entries if entry["id"] == lesson_id), None)
        require(current is not None, f"经验不存在：{lesson_id}")
        body = current["body"] + f"\n\n退休：{date.today().isoformat()} {reason}\n"
        _, entry = _mutated_entries(entries, lesson_id, {
            "status": "retired", "updated_at": date.today().isoformat(), "body": body,
        })
        return _commit_lesson(project, entry, f"chore(lessons): 退休 {entry['id']}", commit)
