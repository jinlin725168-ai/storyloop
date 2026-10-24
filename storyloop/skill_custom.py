"""Read and merge repository and personal customizations for dialogue skills."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import tomllib

from .baseline import BASELINE_PATH, _exclude_control_plane, _git
from .models import StoryLoopError, require
from .path_security import safe_target
from .project_profile import CUSTOM_ROOT
from .skills import skill_text
from .storage import locked_file, write_text_atomic


CUSTOMIZABLE = {"spec-craft"}
VIEW_ROOT = ".storyloop/skills-view"
DEFAULT_QUESTION_LIMIT = 5
MIN_QUESTION_LIMIT = 3
MAX_QUESTION_LIMIT = 10
_TOP_KEYS = {"version", "preferences", "glossary", "coverage", "sections", "question_limit"}
_ROW_KEYS = {
    "coverage": {"code", "name", "check"},
    "sections": {"code", "document", "title", "guidance"},
}


def custom_paths(project: Path, name: str) -> tuple[Path, Path]:
    """Return the team and personal TOML paths for a customizable skill."""
    require(name in CUSTOMIZABLE, f"Skill {name!r} cannot be customized")
    root = Path(project) / CUSTOM_ROOT
    return root / f"{name}.toml", root / f"{name}.user.toml"


def _error(path: str, key: str, message: str) -> StoryLoopError:
    return StoryLoopError(f"{path}: {key}: {message}")


def _read_file(project: Path, path: Path) -> tuple[dict, dict] | None:
    relative = path.relative_to(project).as_posix()
    try:
        target = safe_target(project, relative, "customization")
        if not target.exists():
            return None
        raw = target.read_bytes()
    except (OSError, StoryLoopError) as exc:
        raise _error(relative, "file", str(exc)) from None
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        line = raw[:exc.start].count(b"\n") + 1
        raise StoryLoopError(f"{relative} line {line}: invalid UTF-8") from None
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        line = _toml_error_line(content, exc)
        raise StoryLoopError(f"{relative} line {line}: invalid TOML: {exc}") from None
    source = {"path": relative, "sha256": hashlib.sha256(raw).hexdigest()}
    return data, source


def _toml_error_line(content: str, error: tomllib.TOMLDecodeError) -> int:
    if "end of document" in str(error):
        return max(1, len(content.rstrip("\n").splitlines()))
    line = getattr(error, "lineno", None)
    if line is not None:
        return line
    match = re.search(r"line (\d+)", str(error))
    return int(match.group(1)) if match else max(1, len(content.rstrip("\n").splitlines()))


def _validate_rows(path: str, key: str, value: object) -> list[dict]:
    if not isinstance(value, list):
        raise _error(path, key, "must be an array of tables")
    required = _ROW_KEYS[key]
    rows = []
    for index, row in enumerate(value):
        label = f"{key}[{index}]"
        if not isinstance(row, dict):
            raise _error(path, label, "must be a table")
        unexpected = set(row) - required
        if unexpected:
            raise _error(path, f"{label}.{sorted(unexpected)[0]}", "unknown key")
        missing = required - set(row)
        if missing:
            raise _error(path, f"{label}.{sorted(missing)[0]}", "required key")
        for field in required:
            if not isinstance(row[field], str) or not row[field].strip():
                raise _error(path, f"{label}.{field}", "must be a non-empty string")
        if not re.fullmatch(r"[a-z0-9-]+", row["code"]):
            raise _error(path, f"{label}.code", "use lowercase letters, digits and hyphens")
        if key == "sections" and row["document"] not in {"requirement", "spec"}:
            raise _error(path, f"{label}.document", "must be requirement or spec")
        rows.append(row)
    return rows


def _validated(project: Path, path: str, data: dict) -> dict:
    unexpected = set(data) - _TOP_KEYS
    if unexpected:
        raise _error(path, sorted(unexpected)[0], "unknown key")
    if type(data.get("version")) is not int or data["version"] != 1:
        raise _error(path, "version", "must be 1")
    question_limit = data.get("question_limit")
    if question_limit is not None and (
        type(question_limit) is not int or not MIN_QUESTION_LIMIT <= question_limit <= MAX_QUESTION_LIMIT
    ):
        raise _error(path, "question_limit", f"must be an integer from {MIN_QUESTION_LIMIT} to {MAX_QUESTION_LIMIT}")
    preferences = data.get("preferences", [])
    if not isinstance(preferences, list) or not all(isinstance(item, str) for item in preferences):
        raise _error(path, "preferences", "must be a string array")
    glossary = data.get("glossary")
    if glossary is not None:
        if not isinstance(glossary, str):
            raise _error(path, "glossary", "must be a repository-relative path")
        try:
            target = safe_target(project, glossary, "glossary")
            require(target.is_file(), "Glossary must be an existing file")
        except StoryLoopError as exc:
            raise _error(path, "glossary", str(exc)) from None
    return {
        "question_limit": question_limit,
        "preferences": preferences,
        "glossary": glossary,
        "coverage": _validate_rows(path, "coverage", data.get("coverage", [])),
        "sections": _validate_rows(path, "sections", data.get("sections", [])),
    }


def _merge_rows(existing: list[dict], additions: list[dict]) -> None:
    for row in additions:
        for index, old in enumerate(existing):
            if old["code"] == row["code"]:
                existing[index] = row
                break
        else:
            existing.append(row)


def load_customization(project: Path, name: str) -> dict:
    """Return a validated, stable merge of team then personal customization."""
    project = Path(project).resolve()
    value = {"version": 1, "preferences": [], "glossary": None,
             "coverage": [], "sections": [], "sources": [],
             "question_limit": DEFAULT_QUESTION_LIMIT, "question_limit_source": None}
    for path in custom_paths(project, name):
        parsed = _read_file(project, path)
        if parsed is None:
            continue
        data, source = parsed
        layer = _validated(project, source["path"], data)
        value["sources"].append(source)
        if layer["question_limit"] is not None:
            value["question_limit"] = layer["question_limit"]
            value["question_limit_source"] = source["path"]
        value["preferences"].extend(layer["preferences"])
        if layer["glossary"] is not None:
            value["glossary"] = layer["glossary"]
        for key in ("coverage", "sections"):
            _merge_rows(value[key], layer[key])
    return value


def _render_view(project: Path, name: str, custom: dict) -> str:
    body = skill_text(project, name)
    lines = ["", "", "## 生效配置（由 storyloop skills --view 生成，请勿手工编辑）", ""]
    if custom["sources"]:
        lines.append("来源：" + "、".join(
            f"{source['path']}（sha256 {source['sha256'][:12]}）" for source in custom["sources"]
        ))
    limit_source = custom["question_limit_source"] or "内置默认"
    lines.extend(["", "### 每步骤提问上限",
                  f"步骤 1 评估、2 需求、3 规格、4 澄清、5 交接分别计数，不跨步骤累加；"
                  f"每步骤最多 {custom['question_limit']} 个问题（来源：{limit_source}）。"])
    if custom["preferences"]:
        lines.extend(["", "### 提问偏好", *[f"- {_inline(item)}" for item in custom["preferences"]]])
    if custom["coverage"]:
        lines.extend(["", "### 追加的覆盖地图分类", "| 序号 | 类别 | 检查什么 |", "|---|---|---|"])
        for number, row in enumerate(custom["coverage"], start=_next_coverage_number(body)):
            lines.append(f"| {number} | {_inline(row['name'])} | {_inline(row['check'])} |")
    if custom["sections"]:
        lines.extend(["", "### 追加的模板节"])
        for row in custom["sections"]:
            label = "需求文档" if row["document"] == "requirement" else "规格文档"
            lines.extend([f"#### {label}：{_inline(row['title']).replace('<br>', ' ')}", row["guidance"]])
    if custom["glossary"]:
        lines.extend(["", "### 术语表", f"按 `{custom['glossary']}` 中的术语写规则；新术语先登记。"])
    return body + "\n".join(lines) + "\n"


def _inline(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def _next_coverage_number(body: str) -> int:
    marker = "<!-- references/clarify.md -->"
    require(marker in body, "spec-craft clarification reference is missing")
    clarify = body.split(marker, 1)[1].split("<!-- references/", 1)[0]
    numbers = [int(value) for value in re.findall(r"^\|\s*(\d+)\s*\|", clarify, re.M)]
    require(numbers, "spec-craft coverage map is missing")
    return max(numbers) + 1


def render_view(project: Path, name: str) -> str:
    """Render the packaged or installed skill with append-only project guidance."""
    project = Path(project).resolve()
    return _render_view(project, name, load_customization(project, name))


def write_view(project: Path, name: str) -> dict:
    """Write a reproducible customization view into the control plane."""
    project = Path(project).resolve()
    custom = load_customization(project, name)
    body = _render_view(project, name, custom)
    relative = f"{VIEW_ROOT}/{name}.md"
    target = safe_target(project, relative, "skill view")
    if (project / ".git").is_dir() and not (project / BASELINE_PATH).exists():
        with locked_file(project / ".git/storyloop-init.lock"):
            _exclude_control_plane(project)
            write_text_atomic(target, body)
    else:
        write_text_atomic(target, body)
    warnings = _protect_personal_file(project, name)
    return {
        "status": "SKILL_VIEW", "skill": name, "path": relative,
        "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "customized": bool(custom["sources"]), "sources": custom["sources"], "warnings": warnings,
        "question_limit": custom["question_limit"],
        "question_limit_source": custom["question_limit_source"],
    }


def _protect_personal_file(project: Path, name: str) -> list[str]:
    if not (project / ".git").exists():
        return []
    _, personal = custom_paths(project, name)
    relative = personal.relative_to(project).as_posix()
    tracked = _git(project, "ls-files", "--cached", "--", relative).strip()
    return [f"个人定制 {relative} 已被 Git 跟踪，请移出版本库"] if tracked else []
