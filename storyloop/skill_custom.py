"""Read and merge repository and personal customizations for dialogue skills."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import tomllib

from .models import StoryLoopError, require
from .path_security import safe_target
from .skills import skill_text


CUSTOM_ROOT = "storyloop-custom"
CUSTOMIZABLE = {"spec-craft"}
VIEW_ROOT = ".storyloop/skills-view"
_TOP_KEYS = {"version", "preferences", "glossary", "coverage", "sections"}
_ROW_KEYS = {
    "coverage": {"code", "name", "check"},
    "sections": {"code", "document", "title", "guidance"},
}


def custom_paths(project: Path, name: str) -> tuple[Path, Path]:
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
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        line = raw[:exc.start].count(b"\n") + 1
        raise StoryLoopError(f"{relative} line {line}: invalid UTF-8") from None
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        line = getattr(exc, "lineno", None)
        if line is None:
            match = re.search(r"line (\d+)", str(exc))
            line = int(match.group(1)) if match else max(1, len(content.rstrip("\n").splitlines()))
        raise StoryLoopError(f"{relative} line {line}: invalid TOML: {exc}") from None
    source = {"path": relative, "sha256": hashlib.sha256(raw).hexdigest()}
    return data, source


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
    preferences = data.get("preferences", [])
    if not isinstance(preferences, list) or not all(isinstance(item, str) for item in preferences):
        raise _error(path, "preferences", "must be a string array")
    glossary = data.get("glossary")
    if glossary is not None:
        if not isinstance(glossary, str):
            raise _error(path, "glossary", "must be a repository-relative path")
        try:
            safe_target(project, glossary, "glossary")
        except StoryLoopError as exc:
            raise _error(path, "glossary", str(exc)) from None
    return {
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
             "coverage": [], "sections": [], "sources": []}
    for path in custom_paths(project, name):
        parsed = _read_file(project, path)
        if parsed is None:
            continue
        data, source = parsed
        layer = _validated(project, source["path"], data)
        value["sources"].append(source)
        value["preferences"].extend(layer["preferences"])
        if layer["glossary"] is not None:
            value["glossary"] = layer["glossary"]
        for key in ("coverage", "sections"):
            _merge_rows(value[key], layer[key])
    return value


def _render_view(project: Path, name: str, custom: dict) -> str:
    body = skill_text(project, name)
    if not custom["sources"]:
        return body
    lines = ["", "", "## 项目定制（由 storyloop skills --view 生成，请勿手工编辑）", ""]
    lines.append("来源：" + "、".join(
        f"{source['path']}（sha256 {source['sha256'][:12]}）" for source in custom["sources"]
    ))
    if custom["preferences"]:
        lines.extend(["", "### 提问偏好", *[f"- {item}" for item in custom["preferences"]]])
    if custom["coverage"]:
        lines.extend(["", "### 追加的覆盖地图分类", "| 序号 | 类别 | 检查什么 |", "|---|---|---|"])
        for number, row in enumerate(custom["coverage"], start=11):
            lines.append(f"| {number} | {row['name']} | {row['check']} |")
    if custom["sections"]:
        lines.extend(["", "### 追加的模板节"])
        for row in custom["sections"]:
            label = "需求文档" if row["document"] == "requirement" else "规格文档"
            lines.extend([f"#### {label}：{row['title']}", row["guidance"]])
    if custom["glossary"]:
        lines.extend(["", "### 术语表", f"按 `{custom['glossary']}` 中的术语写规则；新术语先登记。"])
    return body + "\n".join(lines) + "\n"


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
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return {
        "status": "SKILL_VIEW", "skill": name, "path": relative,
        "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "customized": bool(custom["sources"]), "sources": custom["sources"], "warnings": [],
    }
