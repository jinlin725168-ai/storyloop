"""CLI adapter preserving the original init signature and explicit scaffold branch."""
from pathlib import Path

from .baseline import bootstrap_project
from . import adapter_capabilities, init_detect, system_smoke
from .models import require
from .onboarding import scaffold_project
from .storage import load_json


def bootstrap_with_project_checks(project: Path, settings: dict | None = None) -> dict:
    """Bind business preflight and transaction effects to the pure baseline capture."""
    return bootstrap_project(project, settings,
                             smoke_preflight=system_smoke.preflight_system_smoke,
                             smoke_adoption=system_smoke.adopt_system_smoke,
                             adapter_check=adapter_capabilities.check)


def initialize(project: Path, args) -> dict:
    """Dispatch draft generation or original clean baseline locking, never both."""
    require((project / ".git").exists(), "storyloop init needs an existing Git repository (git init first)")
    if args.scaffold:
        require(args.settings is None, "--scaffold cannot be combined with --settings")
        return scaffold_project(project, args.stack)
    require(args.stack is None, "--stack requires --scaffold")
    detect, agent = getattr(args, "detect", False), getattr(args, "agent", None)
    require(not (detect and args.settings), "--detect proposes settings; it cannot be combined with --settings")
    require(not getattr(args, "yes", False) or detect, "--yes confirms --detect")
    agent_name, agent_fields = init_detect.agent_config(agent) if agent else (None, {})
    if detect:
        proposal = init_detect.propose_settings(project)
        if not args.yes:
            return init_detect.proposal_result(project, proposal, agent)
        require(not proposal["gaps"], "Detection could not infer: " + ", ".join(proposal["gaps"])
                + "; write them in a settings file and use --settings")
        settings = proposal["settings"]
    else:
        settings = load_json(args.settings.resolve()) if args.settings else None
    result = bootstrap_with_project_checks(project, settings)
    if not (agent or detect):
        return result  # the original init contract, unchanged
    return _finish_onboarding(project, result, agent_name, agent_fields)


def _finish_onboarding(project: Path, result: dict, agent: str | None, fields: dict) -> dict:
    """Apply the agent to the local (untracked) config, then run readiness once."""
    from .readiness import inspect_readiness
    from .storage import write_json
    if fields:
        config_path = project / ".storyloop/config.json"
        write_json(config_path, {**load_json(config_path), **fields}, max_bytes=512_000)
    readiness = inspect_readiness(project, refresh=True)
    return {**result, "agent": agent, "readiness": readiness["status"],
            "readiness_digest": readiness.get("digest"),
            "next": init_detect.readiness_next(project, readiness)}


def add_init_arguments(child) -> None:
    """Register explicit draft options without changing existing --settings semantics."""
    child.add_argument("--scaffold", action="store_true", help="只生成待人工补全的接入草案")
    child.add_argument("--detect", action="store_true",
                       help="从仓库布局推断工程画像并给出建议（不写任何文件）；加 --yes 确认后锁定底座并跑准备度")
    child.add_argument("--yes", action="store_true", help="确认 --detect 的建议")
    child.add_argument("--agent", choices=["claude", "codex", "auto"],
                       help="配置代理：claude（Claude Code）、codex，或 auto（按 PATH 上能找到的选）")
    child.add_argument("--stack", action="append", choices=["maven", "node", "python", "generic"])
    child.add_argument("--settings", type=Path,
                       help="可选 JSON 工程画像：unit_commands（旧名 test_commands）、integration_commands、"
                            "check_commands、test_roots、module_roots、integration_paths、"
                            "candidate_allow、scratch_paths")
