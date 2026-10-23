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
