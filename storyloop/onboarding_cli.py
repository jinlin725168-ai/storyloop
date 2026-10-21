"""CLI adapter preserving the original init signature and explicit scaffold branch."""
from pathlib import Path

from .baseline import bootstrap_project
from .models import require
from .onboarding import scaffold_project
from .storage import load_json


def initialize(project: Path, args) -> dict:
    """Dispatch draft generation or original clean baseline locking, never both."""
    require((project / ".git").exists(), "storyloop init needs an existing Git repository (git init first)")
    if args.scaffold:
        require(args.settings is None, "--scaffold cannot be combined with --settings")
        return scaffold_project(project, args.stack)
    require(args.stack is None, "--stack requires --scaffold")
    settings = load_json(args.settings.resolve()) if args.settings else None
    return bootstrap_project(project, settings)


def add_init_arguments(child) -> None:
    """Register explicit draft options without changing existing --settings semantics."""
    child.add_argument("--scaffold", action="store_true", help="只生成待人工补全的接入草案")
    child.add_argument("--stack", action="append", choices=["maven", "node", "python", "generic"])
    child.add_argument("--settings", type=Path,
                       help="可选 JSON 工程画像：unit_commands（旧名 test_commands）、integration_commands、"
                            "check_commands、test_roots、module_roots、integration_paths、"
                            "candidate_allow、scratch_paths")
