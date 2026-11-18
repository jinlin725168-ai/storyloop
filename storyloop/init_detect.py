"""One-command onboarding (roadmap F2): propose the profile and the agent, lock, check readiness.

`init --detect` only proposes: which directories a change may edit is a person's decision, so
nothing is written until `--yes`. The proposal is read from the repository's own layout and
build files; anything it cannot infer is left out, never guessed into the baseline.
"""
from __future__ import annotations

from pathlib import Path
import shlex
import shutil

from .models import StoryLoopError, require
from .onboarding_discovery import safe_discovery
from .project_profile import SKIP_DIRECTORIES

TEST_DIRECTORIES = ("tests", "test", "spec", "__tests__")
SCRATCH = {"python": ["*.egg-info", ".hypothesis", ".pytest_cache", "__pycache__", "build"],
           "node": ["coverage", "node_modules"], "maven": ["target"], "gradle": [".gradle", "build"]}
SOURCE_DIRECTORIES = ("src", "lib", "app")
AGENTS = {"claude": {"adapter": "command", "agent_command": ["{python}", "-m", "storyloop.claude_agent"]},
          "codex": {"adapter": "codex", "agent_command": []}}


def propose_settings(project: Path) -> dict:
    """Return init settings plus the evidence each proposed value came from."""
    project = Path(project).resolve()
    detected = safe_discovery(project)
    kinds = detected["kinds"]
    tests = [name for name in TEST_DIRECTORIES if (project / name).is_dir()]
    sources = _source_roots(project, kinds, set(tests))
    settings = {"unit_commands": _unit_commands(project, kinds, detected["unit_commands"], tests),
                "module_roots": sources, "test_roots": tests,
                "scratch_paths": sorted({path for kind in kinds for path in SCRATCH.get(kind, [])})}
    evidence = {"stacks": kinds, "module_roots": "含源码的顶层目录（排除测试目录）",
                "test_roots": "按惯例命名的测试目录", "unit_commands": "由构建文件推断"}
    gaps = [key for key in ("unit_commands", "module_roots") if not settings[key]]
    return {"settings": settings, "evidence": evidence, "gaps": gaps}


def _source_roots(project: Path, kinds: list[str], tests: set[str]) -> list[str]:
    roots = []
    for path in sorted(project.iterdir()):
        name = path.name
        if (not path.is_dir() or path.is_symlink() or name.startswith(".") or name in tests
                or name in SKIP_DIRECTORIES):
            continue
        if "python" in kinds and (path / "__init__.py").is_file():
            roots.append(name)
        elif name in SOURCE_DIRECTORIES:
            roots.append(name)
    return roots


def _unit_commands(project: Path, kinds: list[str], detected: list, tests: list[str]) -> list:
    if "python" in kinds and _uses_pytest(project, tests):
        return [["{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests[:1]]]
    return detected


def _uses_pytest(project: Path, tests: list[str]) -> bool:
    if any((project / name).is_file() for name in ("pytest.ini", "conftest.py")):
        return True
    if any((project / test / "conftest.py").is_file() for test in tests):
        return True
    for name in ("pyproject.toml", "setup.cfg", "tox.ini"):
        path = project / name
        if path.is_file() and "[tool.pytest" in path.read_text(encoding="utf-8", errors="replace"):
            return True
    return False


def agent_config(name: str, which=shutil.which) -> tuple[str, dict]:
    """Map --agent to config fields; `auto` picks the CLI found on PATH (Claude Code first)."""
    if name == "auto":
        found = next((agent for agent in ("claude", "codex") if which(agent)), None)
        if found is None:
            raise StoryLoopError("No agent CLI found on PATH: install Claude Code (claude) or Codex "
                                 "(codex), or pass --agent explicitly", code="AGENT_UNAVAILABLE")
        name = found
    require(name in AGENTS, f"Unsupported agent: {name}")
    return name, dict(AGENTS[name])


def proposal_result(project: Path, proposal: dict, agent: str | None) -> dict:
    flags = ["--detect", "--yes"] + (["--agent", agent] if agent else [])
    return {"status": "SETTINGS_PROPOSED", "initialized": False, "project": str(project), **proposal,
            "next": "确认这些设置（module_roots 是功能变更能改的目录）后执行：" + shlex.join(
                ["storyloop", "init", str(project), *flags])
            + "；要调整就把 settings 存成 JSON，改用 --settings。"}


def readiness_next(project: Path, readiness: dict) -> str:
    """The first decision after locking: approve readiness gaps, fix blockers, or start a change."""
    status, where = readiness.get("status"), str(project)
    if status == "PROJECT_NOT_READY":
        return (f"测试准备度未通过（{', '.join(readiness.get('blockers') or [])}）：修好测试命令后执行 "
                + shlex.join(["storyloop", "readiness", where]))
    start = shlex.join(["storyloop", "change", where, "--idea", "<一句话需求>"])
    if status == "READINESS_APPROVAL_REQUIRED":
        gaps = "、".join(readiness.get("gaps") or [])
        return (f"项目没有 {gaps} 层；确认接受这一缺口：" + shlex.join(
            ["storyloop", "approve", where, "--readiness", readiness.get("digest", "<digest>"),
             "--reason", "<缺口理由>"]) + f"，然后 {start}")
    return start
