"""Capability coverage manifest (roadmap D1): each public command and stop type names its tests.

Every CLI subcommand names a positive test and a boundary (error-path) test, and every stop status
names a test that reaches it. Whatever has none is declared in GAPS with a reason instead of being
left out. tests/test_coverage_manifest.py checks the manifest against the CLI parser and STOP_CLASSES,
that every named test exists, and that its body mentions the command (as a CLI argument or one of its
handler symbols) or the stop status.
"""

COMMANDS = {
    'accept': {'symbols': ['record_manual_acceptance'], 'positive': 'tests.test_base_trust.BaseTrustTests.test_clean_autocrlf_checkout_allows_base_manual_acceptance', 'boundary': 'tests.test_lifecycle_guards.VerificationGuardsTests.test_g2_manual_acceptance_is_bound_to_current_candidate'},
    'acceptance-repair': {'symbols': ['repair_smoke'], 'positive': 'tests.test_characterize_acceptance_repair_next.AcceptanceRepairCharacterizationTests.test_unproven_red_keeps_existing_stop', 'boundary': 'tests.test_bench_correction.BenchCorrectionTests.test_second_oracle_stop_needs_human_without_second_repair'},
    'adapters': {'symbols': [], 'positive': 'tests.test_adapter_capabilities.AdapterCapabilityTests.test_failed_handshake_explains_exit_without_process_output', 'boundary': 'tests.test_adapter_capabilities.AdapterCapabilityTests.test_failed_handshake_never_exposes_stderr_or_stdout'},
    'answer': {'symbols': ['record_answers'], 'positive': 'tests.test_design_approval.DesignApprovalTests.test_answer_never_authorizes_design', 'boundary': 'tests.test_design_approval.DesignApprovalTests.test_answer_never_authorizes_design'},
    'approve': {'symbols': [], 'positive': 'tests.test_routing_cli.RoutingCliTests.test_dispatch_routing_leaves_legacy_commands_to_original_dispatch', 'boundary': 'tests.test_routing_cli.RoutingCliTests.test_readiness_json_exit_codes_and_no_product_before_ready'},
    'assess': {'symbols': ['create_assessment'], 'positive': 'tests.test_spec_craft_batch3.AssessTests.test_assess_records_the_three_answers_and_the_human_decision', 'boundary': 'tests.test_spec_craft_batch3.AssessTests.test_drop_creates_no_change_and_a_missing_answer_is_refused'},
    'authorize-knowledge': {'symbols': [], 'positive': 'tests.test_knowledge_delta.KnowledgeDeltaTests.test_cli_has_both_commands_and_empty_paths_mean_reject'},
    'base-build-config': {'symbols': [], 'positive': 'tests.test_base_supplement.BaseSupplementTests.test_old_project_uses_confirmed_supplement_through_complete_human_base_run', 'boundary': 'tests.test_base_supplement.BaseSupplementTests.test_old_project_uses_confirmed_supplement_through_complete_human_base_run'},
    'base-propose': {'symbols': ['propose_base_change'], 'positive': 'tests.test_base_compatibility.BaseCompatibilityTests.test_proposed_base_update_imports_without_changing_old_file', 'boundary': 'tests.test_base_contract.BaseContractTests.test_control_plane_path_cannot_enter_base_proposal'},
    'base-review': {'symbols': [], 'positive': 'tests.test_base_lifecycle.BaseG1Tests.test_cli_records_named_base_review'},
    'base-update': {'symbols': ['confirmed_upgrade'], 'positive': 'tests.test_oracle_skill_approval.OracleSkillApprovalTests.test_confirmed_receipt_recovers_without_extending_approval', 'boundary': 'tests.test_oracle_skill_approval.OracleSkillApprovalTests.test_confirmed_receipt_recovers_without_extending_approval'},
    'bench': {'symbols': [], 'positive': 'tests.test_bench_calibrate.BenchCalibrationTests.test_v2_cli_calibrates_without_approval'},
    'change': {'symbols': [], 'positive': 'tests.test_semantic_repair.SemanticRepairIntegrationTests.test_change_repairs_one_semantically_invalid_product', 'boundary': 'tests.test_routing_cli.ProductBoundaryTests.test_entry_blocked_draft_exposes_stop_on_change'},
    'ci': {'symbols': [], 'positive': 'tests.test_ci_results.CiResultTests.test_cli_record_flag_records_returned_ci_result'},
    'document': {'symbols': ['document_change'], 'positive': 'tests.test_openspec_docs.ArchiveTests.test_passed_change_commits_delta_design_decisions_and_living_spec', 'boundary': 'tests.test_openspec_docs.ArchiveTests.test_passed_change_commits_delta_design_decisions_and_living_spec'},
    'drafts': {'symbols': [], 'positive': 'tests.test_drafts.DraftTests.test_drafts_cli_lists_and_rejects', 'boundary': 'tests.test_drafts.DraftTests.test_drafts_cli_requires_reason_only_for_rejection'},
    'init': {'symbols': [], 'positive': 'tests.test_baseline_cli.BaselineCliTests.test_init_bootstraps_an_existing_clean_git_repository', 'boundary': 'tests.test_characterize_init_run_hints.InitRunHintsCharacterizationTests.test_existing_baseline_keeps_generic_next'},
    'knowledge-propose': {'symbols': [], 'positive': 'tests.test_knowledge_delta.KnowledgeDeltaTests.test_cli_has_both_commands_and_empty_paths_mean_reject'},
    'lessons': {'symbols': ['confirm_lesson', 'retire_lesson', 'list_lessons'], 'positive': 'tests.test_lesson_store.LessonStoreTests.test_vector_rate_is_none_without_provided_lesson_records', 'boundary': 'tests.test_lesson_store.LessonStoreTests.test_retire_requires_reason_and_appends_it'},
    'lessons-propose': {'symbols': [], 'positive': 'tests.test_retrospective.RetrospectiveTests.test_cli_requires_change', 'boundary': 'tests.test_retrospective.RetrospectiveTests.test_cli_requires_change'},
    'log': {'symbols': [], 'positive': 'tests.test_call_diagnostics.LogAndNextStepTests.test_every_pause_names_its_next_command', 'boundary': 'tests.test_call_diagnostics.LogAndNextStepTests.test_every_pause_names_its_next_command'},
    'map-apply': {'symbols': ['apply_map'], 'positive': 'tests.test_requirements_map.RequirementsMapTests.test_propose_then_apply_installs_the_map_and_registers_existing_requirements', 'boundary': 'tests.test_requirements_map.MapCommandTests.test_map_apply_without_a_proposal_says_what_to_run'},
    'map-propose': {'symbols': ['propose_map'], 'positive': 'tests.test_requirements_map.MapCommandTests.test_map_propose_runs_the_survey_role_through_the_cli', 'boundary': 'tests.test_requirements_map.RequirementsMapTests.test_unplaced_requirements_bad_areas_and_stale_digests_are_refused'},
    'metrics': {'symbols': [], 'positive': 'tests.test_metrics_command.MetricsCommandTests.test_help_lists_metrics_flags', 'boundary': 'tests.test_metrics_command.MetricsCommandTests.test_help_lists_metrics_flags'},
    'readiness': {'symbols': [], 'positive': 'tests.test_routing_cli.RoutingCliTests.test_readiness_text_shows_failure_summary_under_layer', 'boundary': 'tests.test_routing_cli.RoutingCliTests.test_readiness_text_shows_failure_summary_under_layer'},
    'report': {'symbols': [], 'positive': 'tests.test_report_command.ReportCommandTests.test_report_names_the_change_report_path', 'boundary': 'tests.test_report_command.ReportCommandTests.test_report_without_change_or_report_says_why'},
    'requirements': {'symbols': ['registry_view'], 'positive': 'tests.test_requirements_registry.RequirementsCommandTests.test_requirements_lists_requirements_with_their_rules', 'boundary': 'tests.test_requirements_registry.RequirementsCommandTests.test_requirements_rejects_an_unknown_id'},
    'reset': {'symbols': ['reset_run'], 'positive': 'tests.test_base_restart.BaseRestartTests.test_reset_premerge_uses_same_human_restart_point', 'boundary': 'tests.test_base_restart.BaseRestartTests.test_reset_refuses_to_race_with_an_active_run'},
    'run': {'symbols': [], 'positive': 'tests.test_failure_routing.ReviewRoutingTests.test_cli_forwards_overrule_only_on_the_first_run', 'boundary': 'tests.test_base_lifecycle.BaseG1Tests.test_cli_rejects_removed_base_agent_option'},
    'skills': {'symbols': [], 'positive': 'tests.test_skill_custom.ViewTests.test_cli_view_prints_the_result', 'boundary': 'tests.test_skill_custom.ViewTests.test_empty_cli_view_does_not_install_skills'},
    'smoke-apply': {'symbols': ['apply_smoke_update'], 'positive': 'tests.test_smoke_maintenance.SmokeMaintenanceTests.test_a_red_proposal_is_not_applied_and_the_previous_suite_survives', 'boundary': 'tests.test_smoke_maintenance.SmokeMaintenanceTests.test_apply_needs_a_draft_and_a_clean_tree'},
    'smoke-propose': {'symbols': ['propose_smoke_update'], 'positive': 'tests.test_smoke_maintenance.SmokeMaintenanceTests.test_a_red_proposal_is_not_applied_and_the_previous_suite_survives', 'boundary': 'tests.test_smoke_maintenance.SmokeMaintenanceTests.test_apply_needs_a_draft_and_a_clean_tree'},
    'smoke-repair': {'symbols': ['repair_smoke'], 'positive': 'tests.test_characterize_acceptance_repair_next.AcceptanceRepairCharacterizationTests.test_unproven_red_keeps_existing_stop', 'boundary': 'tests.test_bench_correction.BenchCorrectionTests.test_second_oracle_stop_needs_human_without_second_repair'},
    'status': {'symbols': [], 'positive': 'tests.test_base_lifecycle.BaseG1Tests.test_status_of_legacy_agent_attempt_does_not_offer_old_stop', 'boundary': 'tests.test_routing_cli.RoutingReportTests.test_status_is_readonly_and_exposes_draft_intake_stop'},
    'survey': {'symbols': ['survey_foundation'], 'positive': 'tests.test_foundation_survey.SurveyTests.test_survey_writes_the_knowledge_base_and_opens_a_base_proposal', 'boundary': 'tests.test_foundation_survey.SurveyTests.test_failed_validation_keeps_the_response_and_install_can_retry_from_it'},
}

STOPS = {
    'EXPLORATION_RECOMMENDED': 'tests.test_exploration_stop.ExplorationStopTests.test_unverifiable_goal_stops_with_a_revise_option',
    'AGENT_CONTRACT_INVALID': 'tests.test_adapter_conformance.Conformance.test_schema_violation_is_contract_invalid',
    'AGENT_UNAVAILABLE': 'tests.test_adapter_conformance.Conformance.test_process_failure_is_unavailable',
    'BASE_AGENT_REMOVED': 'tests.test_base_lifecycle.BaseG1Tests.test_cli_legacy_agent_error_does_not_suggest_resuming_old_stop',
    'BASE_CHANGE_REQUIRED': 'tests.test_stop_classes.StopClassTests.test_every_stop_status_maps_to_one_class',
    'BASE_REVIEW_REQUIRED': 'tests.test_base_lifecycle.BaseG1Tests.test_review_rejects_candidate_committer_even_when_author_differs',
    'BOUNDARY_VIOLATION': 'tests.test_stop_report.StopPersistenceTests.test_boundary_pause_passes_an_explicit_phase_to_the_report',
    'BUDGET_EXHAUSTED': 'tests.test_run_transitions.RunTransitionsTests.test_self_stop_is_legal_but_terminal_run_cannot_resume',
    'CHARACTERIZATION_FAILED': 'tests.test_call_diagnostics.ExitCodeTests.test_stops_that_need_a_human_fix_exit_2_and_manual_acceptance_exits_0',
    'CI_PROTECTED': 'tests.test_base_compatibility.BaseCompatibilityTests.test_agent_generated_ci_protected_proposal_requires_new_human_authorization',
    'COST_LIMIT_REACHED': 'tests.test_run_transitions.RunTransitionsTests.test_absent_cost_snapshot_is_not_an_authorized_none_target',
    'DESIGN_APPROVAL_REQUIRED': 'tests.test_design_approval.DesignApprovalTests.test_answer_never_authorizes_design',
    'DESIGN_REVISION_REQUIRED': 'tests.test_design_approval.DesignApprovalTests.test_stale_bindings_rejected_even_when_plan_done',
    'FAILED': 'tests.test_change_report.ChangeReportTests.test_failed_or_unmapped_acceptance_cannot_produce_pass_report',
    'HARNESS_SHADOWED': 'tests.test_smoke.SmokeSuiteTests.test_self_candidate_rejects_inline_helper_import',
    'HUMAN_IMPLEMENTATION_REQUIRED': 'tests.test_base_lifecycle.BaseG1Tests.test_contract_files_written_before_commit_are_recovered',
    'MANUAL_ACCEPTANCE_REQUIRED': 'tests.test_run_transitions.RunTransitionsTests.test_completion_states_only_stop_on_actual_workspace_taint_path',
    'NEEDS_INPUT': 'tests.test_verification_entries.PlanningEntryIntegrationTests.test_light_new_contract_without_seam_path_asks_for_design',
    'NEEDS_SPEC_FIX': 'tests.test_call_diagnostics.ExitCodeTests.test_stops_that_need_a_human_fix_exit_2_and_manual_acceptance_exits_0',
    'NO_PROGRESS': 'tests.test_metrics_compute.ComputeTests.test_stuck_preserves_unrecorded_adapter_instead_of_guessing_from_role',
    'ORACLE_DEFECT': 'tests.test_oracle_tiers.RedByRowTypeTests.test_no_judged_test_is_an_oracle_defect',
    'ORACLE_STRENGTH_PRECHECK': 'tests.test_oracle_strength_precheck.OracleStrengthPrecheckTests.test_must_fix_returns_repairable_oracle_defect',
    'READINESS_APPROVAL_REQUIRED': 'tests.test_readiness.ReadinessTests.test_real_count_and_explicit_gap_approval',
    'WORKSPACE_TAINTED': 'tests.test_workspace_scratch.WorkspaceScratchTests.test_undeclared_ignored_output_still_taints',
}

# (kind, name) -> why it is uncovered. Close a gap by naming its test above and deleting it here.
GAPS = {
    ('command-boundary', 'authorize-knowledge'): '只有命令解析测试，没有授权失败路径的测试',
    ('command-boundary', 'base-review'): '只覆盖记录成功，没有拒绝或非法参数的测试',
    ('command-boundary', 'bench'): 'bench 子命令只覆盖成功路径',
    ('command-boundary', 'ci'): '只覆盖 --record 成功路径',
    ('command-boundary', 'knowledge-propose'): '只有命令解析测试，没有失败路径的测试',
    ('stop', 'INTERRUPTED'): '中断停点没有测试断言',
    ('stop', 'RED_ENVIRONMENT_ERROR'): '只作为 acceptance.reason 出现，没有测试断言该停点',
}
