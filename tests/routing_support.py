"""Complete routing signal fixtures used by routing tests."""
from copy import deepcopy
from pathlib import Path

from tests.test_baseline_behavior import git


_SIGNALS = {
    "nature": "feature",
    "stories": 1,
    "criteria": 4,
    "manual": 0,
    "existing_files": 1,
    "created_files": 0,
    "interfaces": 0,
    "max_references": 0,
    "complete": True,
    "risks": {
        key: "no"
        for key in (
            "sensitive",
            "migration",
            "external_contract",
            "new_interface",
            "crosscut",
            "principle_deviation",
            "hard_verify_high_cost",
            "dependency_swap",
            "persistent_state",
            "human_choice",
            "guard",
        )
    },
}


def signals(**overrides) -> dict:
    """Return an isolated, complete Signals object with top-level overrides."""
    value = deepcopy(_SIGNALS)
    value.update(overrides)
    return value


def routing_repository(root: Path) -> None:
    """Create the minimal committed application used by routing evidence tests."""
    git(root, "init", "-q")
    git(root, "config", "user.email", "storyloop@example.invalid")
    git(root, "config", "user.name", "StoryLoop Test")
    (root / "app").mkdir()
    (root / "tests").mkdir()
    (root / "app/service.py").write_text("def serve():\n    return 'ok'\n", encoding="utf-8")
    (root / "tests/test_service.py").write_text(
        "import unittest\n\n"
        "class ServiceTests(unittest.TestCase):\n"
        "    def test_service(self):\n"
        "        self.assertTrue(True)\n",
        encoding="utf-8",
    )
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")


def readiness_repository(root: Path, **settings) -> None:
    """Initialize a clean local repository with explicit readiness test layers."""
    from storyloop.baseline import bootstrap_project
    routing_repository(root)
    bootstrap_project(root, {
        "unit_commands": [["{python}", "-m", "unittest", "discover", "-s", "tests"]],
        "integration_commands": [], "check_commands": [], **settings,
    })
