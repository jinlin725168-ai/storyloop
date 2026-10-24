"""Test suites for tests/run.py.

`core` runs on every pull request and push: the main flow end to end, the safety boundaries,
the command line, and every module that finishes in a few seconds. `full` is every
tests/test_*.py module; CI runs it daily and on demand. A new module belongs to `full` only
until its author adds it here.
"""

# Pure logic and small fixtures: each module took under 3 s on Linux (2026-10-20 timing).
FAST = [
    "tests.test_architecture", "tests.test_invariants", "tests.test_lifecycle_architecture", "tests.test_lifecycle_rules", "tests.test_lifecycle_guards", "tests.test_run_transitions",
    "tests.test_acceptance_layout", "tests.test_adapter_capabilities", "tests.test_adapter_conformance",
    "tests.test_baseline_api", "tests.test_baseline_behavior", "tests.test_baseline_cli",
    "tests.test_baseline_contract", "tests.test_candidate_allow", "tests.test_change_approval",
    "tests.test_change_report", "tests.test_changes_behavior", "tests.test_changes_contract",
    "tests.test_ci_runner", "tests.test_claude_adapter", "tests.test_claude_role_profiles",
    "tests.test_codex_adapter", "tests.test_core", "tests.test_correction_views",
    "tests.test_failure_attribution", "tests.test_final_role_boundaries", "tests.test_forbidden_path_legacy",
    "tests.test_foundation_survey", "tests.test_foundation_quality", "tests.test_git_command", "tests.test_git_identity", "tests.test_init_git_control", "tests.test_monorepo_detection",
    "tests.test_metrics_rules", "tests.test_metrics_compute",
    "tests.test_product_spec", "tests.test_project_agents", "tests.test_project_git_lifecycle",
    "tests.test_project_profile", "tests.test_project_profile_behavior", "tests.test_report_call_provenance",
    "tests.test_report_evidence", "tests.test_report_final_fixes", "tests.test_report_git_process", "tests.test_report_focus",
    "tests.test_report_presentation", "tests.test_report_rendering", "tests.test_role_context_integration",
    "tests.test_role_profiles", "tests.test_role_schemas", "tests.test_role_subagents",
    "tests.test_routing_policy", "tests.test_routing_risks", "tests.test_skill_selection",
    "tests.test_skills", "tests.test_spec_craft", "tests.test_smoke", "tests.test_state_format", "tests.test_stop_report",
    "tests.test_onboarding", "tests.test_onboarding_lock", "tests.test_smoke_harness",
    "tests.test_smoke_harness_protocol", "tests.test_smoke_harness_scratch",
]

# The main flow end to end: contract, RED, implementation, review, report, merge.
END_TO_END = [
    "tests.test_product_e2e", "tests.test_nature_e2e", "tests.test_change_types",
    "tests.test_reports_e2e", "tests.test_roles_e2e",
    "tests.test_project_loop", "tests.test_lifecycle_golden", "tests.test_lifecycle_recovery_entries",
]

# Safety boundaries: out-of-scope writes, tampering, path and process containment.
SAFETY = [
    "tests.test_landing_recovery",
    "tests.test_boundaries", "tests.test_project_boundaries", "tests.test_project_security_gates",
    "tests.test_project_loop_tampering", "tests.test_process_containment", "tests.test_workspace_scratch",
    "tests.test_ci_path_safety", "tests.test_windows_path_guards", "tests.test_oracle_protection",
    "tests.test_retrospective_protocol", "tests.test_onboarding_transactions",
    "tests.test_onboarding_safety", "tests.test_onboarding_migration", "tests.test_smoke_harness_lifecycle",
    "tests.test_onboarding_acceptance",
]

# The command line users type.
COMMAND_LINE = ["tests.test_cli", "tests.test_product_cli"]

# Cross-platform regressions: 8.3 short paths, core.autocrlf checkouts, UTF-8 pipes (all under 1 s).
PLATFORM = [
    "tests.test_characterization_baseline", "tests.test_claude_adapter_encoding",
    "tests.test_evidence_root_spelling", "tests.test_line_endings",
    "tests.test_memory_fixture_portability",
]

# The suite runner and this list.
RUNNER = ["tests.test_suites"]

# Invariant guards that live in slower modules (design 2026-10-21 §9.2).
INVARIANTS = ["tests.test_completion_resilience"]

CORE = sorted(set(FAST + END_TO_END + SAFETY + COMMAND_LINE + PLATFORM + RUNNER + INVARIANTS))
