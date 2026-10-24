"""Frozen values and pure validators shared by stored state and role schemas."""

import re

from .errors import StoryLoopError, require


MAX_SUMMARY = 120
STRENGTHS = ("prose", "knowledge", "check", "config")
TIERS = ("core", "normal")
STATUSES = ("active", "needs-review", "retired")
ACTIONS = ("create", "update", "retire")
LANDING_KINDS = ("foundation", "check_command", "principles")
RISK_FIELDS = (
    "sensitive", "migration", "external_contract", "new_interface", "crosscut",
    "principle_deviation", "hard_verify_high_cost", "dependency_swap", "persistent_state",
    "human_choice", "guard",
)
MANIFEST = "acceptance/onboarding.json"
SETTINGS = "acceptance/storyloop-settings.json"
GUIDE = "acceptance/AGENT_GUIDE.md"
HOOKS = ("build", "start", "ready", "run_case", "cleanup")
ONBOARDING_STACKS = ("maven", "node", "python", "generic")
SYSTEM_SMOKE_DIR = "acceptance/smoke"
LEGACY_CHANGE_DIR = re.compile(r"^[A-Z]+-\d{8}-\d{3}$")
ROW_TYPES = ("positive", "degradation", "unknown_state", "form_parity",
             "regression", "state_preservation", "non_goal")
# Changed-behaviour rows must fail before implementation; preservation rows must pass.
RED_ROWS = frozenset({"positive", "degradation", "unknown_state", "form_parity"})
ORACLE_CLASSES = ("auto-fast", "auto-heavy", "manual")
LOCATION = re.compile(r"^[^:\s][^:]*:[1-9][0-9]*$")


def criterion_expects_red(criterion: dict) -> bool:
    return criterion.get("row_type", "positive") in RED_ROWS


def criterion_tier(criterion: dict) -> str:
    """fast | heavy | manual, from oracle_class or kind when absent."""
    default = "manual" if criterion.get("kind") == "manual" else "auto-fast"
    oracle_class = criterion.get("oracle_class", default)
    return {"auto-fast": "fast", "auto-heavy": "heavy", "manual": "manual"}[oracle_class]


def is_system_smoke_path(relative: str) -> bool:
    """Exclude legacy per-change suites nested below the system smoke directory."""
    if not relative.startswith(SYSTEM_SMOKE_DIR + "/"):
        return False
    first = relative[len(SYSTEM_SMOKE_DIR) + 1:].split("/", 1)[0]
    return LEGACY_CHANGE_DIR.match(first) is None


def validate_test_count_patterns(value: object) -> list[dict]:
    """Validate settings patterns with one named executed-count capture."""
    require(isinstance(value, list), "test_count_patterns must be a list")
    result = []
    for item in value:
        require(isinstance(item, dict) and set(item) == {"match", "executed"},
                "Each test count pattern needs exactly match and executed")
        require(isinstance(item["match"], str) and bool(item["match"]), "pattern match must be nonempty text")
        try:
            compiled = re.compile(item["executed"], re.M)
        except (TypeError, re.error) as exc:
            raise StoryLoopError(f"Invalid test count pattern: {exc}") from None
        require("executed" in compiled.groupindex, "A test count pattern needs a named group 'executed'")
        result.append({"match": item["match"], "executed": item["executed"]})
    return result
