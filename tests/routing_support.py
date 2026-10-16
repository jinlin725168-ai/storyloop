"""Complete routing signal fixtures used by routing tests."""
from copy import deepcopy


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
