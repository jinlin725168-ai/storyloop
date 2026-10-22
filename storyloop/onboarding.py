"""Two-stage repository onboarding: draft first, clean committed foundation later."""
from pathlib import Path

from .baseline import BASELINE_PATH, _git, _require_local_git_directory
from .models import require
from .onboarding_templates import HINTS, MANIFEST, render
from .onboarding_manifest import validate_onboarding
from .onboarding_install import install as _install
from .path_security import safe_target
from .onboarding_discovery import safe_discovery


def scaffold_project(project: Path, stacks: list[str] | None = None) -> dict:
    """Generate drafts only; return explicit selection requirements for ambiguous stacks."""
    project = Path(project).resolve()
    _check_repository(project)
    head = _git(project, "rev-parse", "HEAD").strip()
    branch = _git(project, "branch", "--show-current").strip()
    detected = safe_discovery(project)
    suggestions = sorted({kind if kind in HINTS else "generic" for kind in detected["kinds"]})
    if stacks is None and (len(suggestions) != 1 or suggestions == ["generic"]):
        return {"status": "SCAFFOLD_SELECTION_REQUIRED", "initialized": False,
                "suggested_stacks": suggestions, "detected": detected["kinds"],
                "next": "未初始化；显式 --stack 选择栈，未知/Gradle 请选 generic。"}
    selected = sorted(set(stacks or suggestions))
    require(bool(selected) and all(stack in HINTS for stack in selected), "Unsupported scaffold stack")
    artifacts = render(selected)
    require(_git(project, "rev-parse", "HEAD").strip() == head, "Repository HEAD changed during scaffold")
    if (project / MANIFEST).exists():
        _unchanged(project, artifacts)
        from .onboarding_install import require_draft_state
        require_draft_state(project, head, branch, set(artifacts))
        status = "SCAFFOLD_UNCHANGED"
    else:
        require(not _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
                "Git worktree must be clean before scaffold")
        _install(project, artifacts, head, branch)
        status = "SCAFFOLD_CREATED"
    return {"status": status, "initialized": False, "project": str(project),
            "stacks": selected, "suggested_stacks": suggestions, "files": list(artifacts),
            "hints": {stack: HINTS[stack] for stack in selected},
            "next": "未初始化、需人工补全：填写 hook/settings/规范，离线验证并提交，再执行原 init。"}


def _check_repository(project: Path) -> None:
    require(project.is_dir(), "Git project must exist")
    _require_local_git_directory(project)
    require(not (project / BASELINE_PATH).exists(), "Existing baseline cannot be scaffolded; use BASE")
    require(Path(_git(project, "rev-parse", "--show-toplevel").strip()).resolve() == project,
            "Project must be the Git repository root")
    require(bool(_git(project, "branch", "--show-current").strip()), "Named branch required")


def _unchanged(project: Path, artifacts: dict[str, bytes]) -> None:
    for relative, content in artifacts.items():
        target = safe_target(project, relative, "scaffold target")
        require(target.is_file() and target.read_bytes().replace(b"\r\n", b"\n") == content,
                f"Scaffold conflict; preserve manual changes: {relative}")
