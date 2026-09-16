"""Strict contracts for the stage-oriented Loop v2 foundation."""

import unittest

from storyloop.models import StoryLoopError
from storyloop.stage_config import select_adapter, stage_order, validate_loop_config


def loop_config() -> dict:
    return {
        "version": 2,
        "agent": {
            "default": "codex",
            "adapters": {
                "codex": {"kind": "codex", "command": []},
                "claude": {"kind": "command", "command": ["claude", "--json"]},
                "manual": {"kind": "manual", "command": []},
            },
            "timeout_seconds": 300,
            "environment_allowlist": ["PATH", "JAVA_HOME"],
        },
        "budgets": {
            "attempts_per_stage": 5,
            "total_seconds": 1800,
            "repeated_failure_limit": 2,
            "backoff_seconds": [2, 5, 10, 20],
        },
        "stages": [
            {
                "id": "foundation",
                "depends_on": [],
                "specs": ["openspec/changes/foundation/proposal.md"],
                "write_paths": ["service/business/**"],
                "protected_paths": ["acceptance/**", "openspec/**"],
                "judges": [
                    {"id": "red", "phase": "red", "command": ["python", "acceptance/check.py", "red"]},
                    {"id": "core", "phase": "l1", "command": ["python", "acceptance/check.py", "core"]},
                    {"id": "full", "phase": "l2", "command": ["python", "acceptance/check.py", "full"]},
                ],
            }
        ],
    }


class LoopConfigTests(unittest.TestCase):
    def assert_invalid(self, value: dict, message: str | None = None) -> None:
        with self.assertRaises(StoryLoopError) as caught:
            validate_loop_config(value)
        if message:
            self.assertIn(message, str(caught.exception))

    def test_valid_config_preserves_contract_and_orders_stages(self):
        value = loop_config()
        second = {**value["stages"][0], "id": "delivery", "depends_on": ["foundation"],
                  "write_paths": ["api/business/**"]}
        value["stages"].append(second)

        self.assertIs(validate_loop_config(value), value)
        self.assertEqual([stage["id"] for stage in stage_order(value)], ["foundation", "delivery"])

    def test_adapter_selection_precedence_is_cli_then_environment_then_default(self):
        value = loop_config()
        validate_loop_config(value)

        self.assertEqual(select_adapter(value)[0], "codex")
        self.assertEqual(select_adapter(value, environ={"LOOP_AGENT": "claude"})[0], "claude")
        self.assertEqual(select_adapter(value, "manual", {"LOOP_AGENT": "claude"})[0], "manual")
        with self.assertRaisesRegex(StoryLoopError, "Unknown selected adapter"):
            select_adapter(value, "missing")

    def test_rejects_unknown_fields_and_invalid_adapter_registry(self):
        value = loop_config()
        value["unexpected"] = True
        self.assert_invalid(value, "Unknown contract fields")

        value = loop_config()
        value["agent"]["default"] = "missing"
        self.assert_invalid(value, "Default adapter")

        value = loop_config()
        value["agent"]["adapters"]["claude"]["command"] = []
        self.assert_invalid(value, "Command adapter")

    def test_rejects_sensitive_or_duplicate_environment_names(self):
        for name in ("API_KEY", "ACCESS_TOKEN", "DB_PASSWORD", "CLIENT_SECRET", "CREDENTIALS"):
            value = loop_config()
            value["agent"]["environment_allowlist"].append(name)
            self.assert_invalid(value, "Sensitive environment")

        value = loop_config()
        value["agent"]["environment_allowlist"].append("PATH")
        self.assert_invalid(value, "Duplicate environment")

    def test_rejects_duplicate_unknown_and_cyclic_stage_dependencies(self):
        value = loop_config()
        value["stages"].append({**value["stages"][0]})
        self.assert_invalid(value, "Duplicate stage")

        value = loop_config()
        value["stages"][0]["depends_on"] = ["missing"]
        self.assert_invalid(value, "Unknown stage dependency")

        value = loop_config()
        other = {**value["stages"][0], "id": "other", "depends_on": ["foundation"],
                 "write_paths": ["api/business/**"]}
        value["stages"][0]["depends_on"] = ["other"]
        value["stages"].append(other)
        self.assert_invalid(value, "cycle")

    def test_rejects_unsafe_spec_and_boundary_paths(self):
        for path in ("C:/source/**", "/root/**", "../outside/**", "safe\\windows/**",
                     ".storyloop/**", ".GIT/**", "**"):
            value = loop_config()
            value["stages"][0]["write_paths"] = [path]
            self.assert_invalid(value, "write path")

        value = loop_config()
        value["stages"][0]["specs"] = ["openspec/changes/example/spec.json"]
        self.assert_invalid(value, "Markdown")

    def test_rejects_overlapping_write_and_protected_boundaries(self):
        value = loop_config()
        value["stages"][0]["write_paths"] = ["service/**"]
        value["stages"][0]["protected_paths"].append("service/tests/**")
        self.assert_invalid(value, "overlap")

        value = loop_config()
        value["stages"][0]["write_paths"] = ["service/**", "service/business/**"]
        self.assert_invalid(value, "overlap")

    def test_rejects_case_only_boundary_overlap_for_cross_platform_configs(self):
        value = loop_config()
        value["stages"][0]["write_paths"] = ["service/**"]
        value["stages"][0]["protected_paths"].append("Service/Secrets/**")

        self.assert_invalid(value, "overlap")

    def test_requires_every_spec_to_be_inside_a_protected_boundary(self):
        value = loop_config()
        value["stages"][0]["protected_paths"] = ["acceptance/**"]
        self.assert_invalid(value, "Spec paths must be protected")

    def test_requires_one_red_and_at_least_one_l1_and_l2_judge(self):
        for missing in ("red", "l1", "l2"):
            value = loop_config()
            value["stages"][0]["judges"] = [
                judge for judge in value["stages"][0]["judges"] if judge["phase"] != missing
            ]
            self.assert_invalid(value, f"{missing} judge")

        value = loop_config()
        value["stages"][0]["judges"].append(
            {"id": "red-again", "phase": "red", "command": ["python", "acceptance/check.py"]}
        )
        self.assert_invalid(value, "exactly one red")

    def test_rejects_duplicate_judges_and_non_argv_commands(self):
        value = loop_config()
        value["stages"][0]["judges"][1]["id"] = "red"
        self.assert_invalid(value, "Duplicate judge")

        value = loop_config()
        value["stages"][0]["judges"][0]["command"] = "python acceptance/check.py"
        self.assert_invalid(value, "command must be a list")

    def test_rejects_invalid_or_unbounded_budgets(self):
        for field, invalid in (("attempts_per_stage", 0), ("attempts_per_stage", 101),
                               ("total_seconds", 86401), ("repeated_failure_limit", -1)):
            value = loop_config()
            value["budgets"][field] = invalid
            self.assert_invalid(value, f"Invalid {field}")

        value = loop_config()
        value["budgets"]["backoff_seconds"] = [5, 2]
        self.assert_invalid(value, "nondecreasing")

        value = loop_config()
        value["budgets"]["backoff_seconds"].append(30)
        self.assert_invalid(value, "fewer entries")

    def test_rejects_empty_collections_and_non_ascii_identifiers(self):
        for section in ("adapters", "stages"):
            value = loop_config()
            if section == "adapters":
                value["agent"][section] = {}
            else:
                value[section] = []
            self.assert_invalid(value)

        value = loop_config()
        value["stages"][0]["id"] = "功能"
        self.assert_invalid(value, "Invalid stage id")


if __name__ == "__main__":
    unittest.main()


