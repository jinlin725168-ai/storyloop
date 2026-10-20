"""Parse, render, and validate repository lesson entries."""

from __future__ import annotations

from datetime import date
import json
from pathlib import PurePosixPath
import re

from .models import StoryLoopError
from .path_security import forbidden_path_root, repository_relative


LESSONS_ROOT = "openspec/lessons"
MAX_BODY = 4096
MAX_CORE_BODY = 4096
STRENGTHS = ("prose", "knowledge", "check", "config")
TIERS = ("core", "normal")
STATUSES = ("active", "needs-review", "retired")

_FIELDS = (
    "id",
    "summary",
    "tier",
    "scope",
    "roles",
    "strength",
    "source",
    "baseline_commit",
    "status",
    "updated_at",
    "supersedes",
    "landings",
)
_ID = re.compile(r"L-[0-9]{4,}")


def _invalid(filename: str, field: str, reason: str) -> StoryLoopError:
    return StoryLoopError(
        f"经验文件 {filename} 的 {field} 无效：{reason}",
        code="LESSON_INVALID",
    )


def _json_value(raw: str, filename: str, field: str) -> object:
    def reject_constant(value: str) -> object:
        raise ValueError(f"invalid JSON constant {value}")

    try:
        return json.loads(raw, parse_constant=reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise _invalid(filename, field, "值必须是合法 JSON") from exc


def _metadata(lines: list[str], filename: str) -> dict:
    metadata = {}
    for line in lines:
        if ": " not in line:
            field = line.split(":", 1)[0] or "metadata"
            raise _invalid(filename, field, "元数据行必须使用“键: JSON 值”格式")
        field, raw = line.split(": ", 1)
        if not field or field in metadata:
            raise _invalid(filename, field or "metadata", "字段为空或重复")
        metadata[field] = _json_value(raw, filename, field)
    missing = [field for field in _FIELDS if field not in metadata]
    if missing:
        raise _invalid(filename, missing[0], "缺少必填字段")
    extra = [field for field in metadata if field not in _FIELDS]
    if extra:
        raise _invalid(filename, extra[0], "未知字段")
    return metadata


def _nonempty_text(value: object, filename: str, field: str) -> str:
    if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
        raise _invalid(filename, field, "必须是非空单行字符串")
    return value


def _string_list(value: object, filename: str, field: str, *, nonempty: bool) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        raise _invalid(filename, field, "必须是字符串列表")
    if any(not isinstance(item, str) or not item for item in value):
        raise _invalid(filename, field, "必须是字符串列表")
    return value


def _validate_scope(value: object, filename: str) -> None:
    scopes = _string_list(value, filename, "scope", nonempty=True)
    try:
        for scope in scopes:
            if scope != "**":
                forbidden_path_root(scope)
    except StoryLoopError as exc:
        raise _invalid(filename, "scope", str(exc)) from exc


def _landing_fields(
    landing: dict, required: set[str], optional: set[str], filename: str
) -> None:
    if not required <= landing.keys() or not landing.keys() <= required | optional:
        raise _invalid(filename, "landings", "落点字段不完整或含未知字段")


def _validate_landing(landing: object, filename: str) -> None:
    if not isinstance(landing, dict) or not isinstance(landing.get("kind"), str):
        raise _invalid(filename, "landings", "每个落点必须是含 kind 的对象")
    kind = landing["kind"]
    if kind == "check_command":
        _landing_fields(landing, {"kind", "argv"}, {"name"}, filename)
        _string_list(landing["argv"], filename, "landings", nonempty=True)
        if "name" in landing:
            _nonempty_text(landing["name"], filename, "landings")
    elif kind == "foundation":
        _landing_fields(landing, {"kind", "path"}, {"anchor"}, filename)
        path = _nonempty_text(landing["path"], filename, "landings")
        try:
            normalized = repository_relative(path, "foundation path")
        except StoryLoopError as exc:
            raise _invalid(filename, "landings", str(exc)) from exc
        if not normalized.startswith("openspec/foundation/"):
            raise _invalid(filename, "landings", "foundation path 必须位于 openspec/foundation/")
        if "anchor" in landing:
            _nonempty_text(landing["anchor"], filename, "landings")
    elif kind == "principles":
        _landing_fields(landing, {"kind"}, set(), filename)
    else:
        raise _invalid(filename, "landings", f"未知落点类型 {kind}")


def _validate_metadata(metadata: dict, filename: str) -> None:
    lesson_id = _nonempty_text(metadata["id"], filename, "id")
    if _ID.fullmatch(lesson_id) is None:
        raise _invalid(filename, "id", "必须是 L- 加四位以上数字")
    if PurePosixPath(filename).name != f"{lesson_id}.md":
        raise _invalid(filename, "id", "必须与文件名一致")
    summary = _nonempty_text(metadata["summary"], filename, "summary")
    if len(summary) > 120:
        raise _invalid(filename, "summary", "不能超过 120 字符")
    for field, allowed in (("tier", TIERS), ("strength", STRENGTHS), ("status", STATUSES)):
        if metadata[field] not in allowed:
            raise _invalid(filename, field, f"必须是 {', '.join(allowed)} 之一")
    _validate_scope(metadata["scope"], filename)
    _string_list(metadata["roles"], filename, "roles", nonempty=True)
    _nonempty_text(metadata["source"], filename, "source")
    _nonempty_text(metadata["baseline_commit"], filename, "baseline_commit")
    _validate_date(metadata["updated_at"], filename)
    supersedes = _string_list(metadata["supersedes"], filename, "supersedes", nonempty=False)
    if any(_ID.fullmatch(item) is None for item in supersedes):
        raise _invalid(filename, "supersedes", "每项必须是经验 ID")
    _validate_landings(metadata, filename)


def _validate_date(value: object, filename: str) -> None:
    text = _nonempty_text(value, filename, "updated_at")
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise _invalid(filename, "updated_at", "必须是 ISO 日期") from exc
    if parsed.isoformat() != text:
        raise _invalid(filename, "updated_at", "必须是 ISO 日期")


def _validate_landings(metadata: dict, filename: str) -> None:
    landings = metadata["landings"]
    if not isinstance(landings, list):
        raise _invalid(filename, "landings", "必须是列表")
    if metadata["strength"] != "prose" and not landings:
        raise _invalid(filename, "landings", "非 prose 强度必须声明落点")
    for landing in landings:
        _validate_landing(landing, filename)


def _body_size(body: str, filename: str) -> int:
    try:
        return len(body.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise _invalid(filename, "body", "必须可编码为 UTF-8") from exc


def parse_lesson(text: str, filename: str) -> dict:
    """Parse and validate one lesson without accessing repository state."""
    if not isinstance(text, str):
        raise _invalid(filename, "content", "必须是文本")
    normalized = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        raise _invalid(filename, "metadata", "必须以 --- 元数据块开头")
    closing = normalized.find("\n---\n", 4)
    if closing < 0:
        raise _invalid(filename, "metadata", "缺少元数据块结束行")
    remainder = normalized[closing + 5 :]
    if not remainder.startswith("\n"):
        raise _invalid(filename, "body", "元数据与正文之间必须有空行")
    metadata = _metadata(normalized[4:closing].split("\n"), filename)
    _validate_metadata(metadata, filename)
    body = remainder[1:]
    size = _body_size(body, filename)
    if size > MAX_BODY:
        raise _invalid(filename, "body", f"正文不能超过 {MAX_BODY} 字节")
    return {**metadata, "body": body}


def render_lesson(entry: dict) -> str:
    """Render fields in canonical order without validating the entry."""
    keys = [field for field in _FIELDS if field in entry]
    keys.extend(field for field in entry if field not in _FIELDS and field != "body")
    lines = [f"{field}: {json.dumps(entry[field], ensure_ascii=False)}" for field in keys]
    return "---\n" + "\n".join(lines) + "\n---\n\n" + entry.get("body", "")


def validate_lessons(entries: list[dict], roles: set[str]) -> None:
    """Validate relationships and limits across already parsed lessons."""
    by_id = {}
    for entry in entries:
        lesson_id = entry["id"]
        if lesson_id in by_id:
            raise _invalid(f"{lesson_id}.md", "id", f"重复 ID {lesson_id}")
        by_id[lesson_id] = entry
        unknown = set(entry["roles"]) - roles
        if unknown:
            raise _invalid(f"{lesson_id}.md", "roles", f"未知角色 {', '.join(sorted(unknown))}")
    for entry in entries:
        for superseded in entry["supersedes"]:
            target = by_id.get(superseded)
            if target is None or target["status"] != "retired":
                raise _invalid(f"{entry['id']}.md", "supersedes", f"{superseded} 必须存在且已退休")
    total = sum(
        len(entry["body"].encode("utf-8"))
        for entry in entries
        if entry["tier"] == "core" and entry["status"] == "active"
    )
    if total > MAX_CORE_BODY:
        raise _invalid(LESSONS_ROOT, "body", f"active 核心层正文合计不能超过 {MAX_CORE_BODY} 字节")


def scope_matches(scope: list[str], path: str) -> bool:
    """Return whether a repository path falls under any lesson scope."""
    normalized = repository_relative(path)
    for pattern in scope:
        if pattern == "**":
            return True
        root = forbidden_path_root(pattern)
        if normalized == root or normalized.startswith(root + "/"):
            return True
    return False


def next_lesson_id(entries: list[dict]) -> str:
    """Allocate the ID immediately after the largest existing lesson number."""
    highest = max((int(entry["id"][2:]) for entry in entries), default=0)
    return f"L-{highest + 1:04d}"
