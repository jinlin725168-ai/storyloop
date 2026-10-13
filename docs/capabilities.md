# 能力清单

对应引擎版本：0.1.0，核对日期 2026-10-11。每项能力给出入口、实现位置、为什么这样设计（决策记录）和守护它的测试。细节不在这里重述，跟着链接走。

## 一、底座与接入

| 能力 | 入口 | 实现 | 决策 | 测试 |
|---|---|---|---|---|
| 技术栈中立的工程探测（根目录与一级子目录的 Maven、Gradle、Node、Python） | `init` | `project_profile.detect_project` | — | `test_project_profile*`、`test_monorepo_detection` |
| 锁定逻辑底座：已跟踪文件指纹、可变区、集成点、CI 保护 | `init [--settings]` | `baseline.bootstrap_project` | [0004](decisions/0004-foundation-vs-mutable-area.md) | `test_baseline_*` |
| 底座漂移检查（文件指纹、索引标志、跟踪范围） | 每次 `run` 开始 | `baseline.verify_baseline` | [0004](decisions/0004-foundation-vs-mutable-area.md) | `test_project_security_gates` |
| BASE 提案与回归门控升级（含上游同步、CI 由人纳入） | `base-propose`、`base-update` | `base_upgrade` | [0003](decisions/0003-single-repo-with-subtree.md)、[0004](decisions/0004-foundation-vs-mutable-area.md) | `test_base_propose`、`test_base_upgrade`、`test_resume_after_base_upgrade` |
| 底座知识库：survey 角色摸底产出八份文档，受 BASE 保护，每个角色先读 | `survey [--from]` | `foundation_survey` | [0013](decisions/0013-foundation-knowledge-base.md) | `test_foundation_survey` |
| 引擎自带技能包装进项目（Claude Code 与 Codex 通用） | `skills` | `skills` | [0014](decisions/0014-skills-as-packaged-modules.md) | `test_skills` |

## 二、需求合同

| 能力 | 入口 | 实现 | 决策 | 测试 |
|---|---|---|---|---|
| 一句话意图生成 spec-kit 结构的合同（优先级、独立验证、EARS 规则、Gherkin 验收条件） | `change --idea` | `cli._change`、`changes.create_change`、product 角色 | [0006](decisions/0006-industry-practices-over-local-plugins.md) | `test_product_spec`、`test_changes_*` |
| 终端选择题澄清（每轮至多 5 题，回车选推荐项），回答记入 `clarifications` | `change`（终端） | `cli.clarify_interactively` | — | `test_product_spec` |
| 修订：任何修订让旧批准失效 | `change --change --message` | `changes.revise_change`、`contract_digest` | — | `test_change_approval` |
| 确定性需求质量门（checklist.md），未决问题阻止批准 | `approve` | `requirement_checklist`、`approvals.approve_change` | — | `test_spec_gates` |
| OpenSpec 增量规格、`openspec validate` 门（装了 CLI 才跑） | 批准后的 `run` | `openspec_docs`、`approvals.materialize_approved_change` | [0007](decisions/0007-change-dir-never-archived.md) | `test_openspec_docs`、`test_spec_gates` |

## 三、闭环执行

| 能力 | 入口 | 实现 | 决策 | 测试 |
|---|---|---|---|---|
| 外部闭环持有判定权，代理声明不是证据 | `approve`、`run` | `project_loop.run_change` | [0001](decisions/0001-external-loop-owns-verdict.md) | `test_project_loop*`、`test_product_e2e` |
| 变更验收套件实现前盲写、与合同一起提交、候选副本里证明 RED；实现前就已通过（全部或部分条件）停在 `EXISTING_GREEN` | 批准后自动 | `project_loop._prepare_acceptance`、`smoke`、`smoke_runner` | [0002](decisions/0002-smoke-before-implementation.md)、[0008](decisions/0008-smoke-core-flow-only.md) | `test_smoke`、`test_acceptance_layout` |
| design（架构师）只读出方案与 `touched`；故事数少于 `design_min_stories`（默认 2）时跳过 | RED 之后 | `project_loop_gates.ensure_design` | [0016](decisions/0016-roles-by-authority.md) | `test_design_role` |
| 特征化门：既有文件改动前先用绿测试锁定现有行为；未声明的改动还原、补锁、重试；跑红时人就地修正后续跑 | 方案之后 | `project_loop_gates.ensure_characterization`、`uncharacterized_paths`、`resume_characterization` | [0015](decisions/0015-test-layers.md) | `test_characterization` |
| 实现—验证—审查循环，失败回喂；`max_attempts` 是唯一质量预算 | 每轮尝试 | `project_loop._run_attempt` | [0005](decisions/0005-budget-semantics.md)、[0012](decisions/0012-implementation-tdd-by-prompt.md) | `test_project_loop` |
| 五层验证各出结论：单元、集成、变更验收、系统冒烟、静态检查 | 每轮尝试 | `project_loop._run_checks_safely`、`layers_green` | [0015](decisions/0015-test-layers.md) | `test_test_layers` |
| 边界审计与 BASE 停点；控制面、暂存、未声明忽略文件判 `WORKSPACE_TAINTED`；`scratch_paths` 放行构建产物 | 每次代理调用前后 | `project_boundaries`、`boundaries`、`project_loop_guards.write_safely` | [0004](decisions/0004-foundation-vs-mutable-area.md) | `test_project_boundaries`、`test_boundaries`、`test_workspace_scratch`、`test_project_loop_tampering` |
| 独立审查（只读），`fail` 回喂、`needs_input` 停点 | 验证全绿后 | `project_loop._perform_review`、`project_contracts.validate_review` | [0001](decisions/0001-external-loop-owns-verdict.md) | `test_project_loop` |
| 问答停点：代理提问停在 `NEEDS_INPUT`，终端直接问或 `answer`，回答进后续调用 | `run`、`answer` | `answers`、`cli.run_with_answers` | — | `test_answers` |
| 人工验收停点：`manual` 条件逐条 `accept`，记录绑定实现指纹 | `accept` | `manual_acceptance`、`project_loop._manual_acceptance_gate` | [0010](decisions/0010-implementation-digest.md)、[0017](decisions/0017-implementation-digest-scope.md) | `test_manual_acceptance` |
| 验收用例缺陷通道：acceptance 角色修自己的套件（含 `EXISTING_GREEN` 时加强断言），从合同提交重证 RED | `acceptance-repair`（旧名 `smoke-repair`） | `acceptance_repair.repair_smoke` | [0009](decisions/0009-smoke-repair-channel.md) | `test_smoke_repair`、`test_project_security_gates` |
| 人工复位卡住的运行，理由留痕 | `reset` | `run_recovery.reset_run` | — | `test_run_reset` |
| 幂等完成阶段：实现提交、文档、报告、no-ff 合并、回执，崩溃后续跑不重复 | 审查通过后 | `project_loop_finish`、`project_receipt` | [0011](decisions/0011-no-push-no-history-rewrite.md) | `test_completion_resilience`、`test_project_loop_resume`、`test_project_loop_recovery_security` |

| 变更性质：feature / fix / refactor，各自的合同块与判据；ID 前缀随性质 | `change`、`approve` | `models`、`requirement_checklist`、`changes` | — | `test_change_nature`、`test_nature_e2e` |
| 行类型决定实现前状态：应红的红、应绿的绿；应绿的先失败停在 `ORACLE_DEFECT` | RED 证明 | `smoke_runner._judge_red`、`project_loop._prepare_acceptance` | — | `test_oracle_tiers` |
| 判据自证：执行数统计、旧代码基线、零执行与下降判失败 | 每轮验证 | `oracle_counts` | — | `test_oracle_counts`、`test_oracle_baseline` |
| 判据分级：快判据每轮，重判据在快判据全绿后 | 每轮验证 | `smoke_runner._run_tiered` | — | `test_oracle_tiers` |
| 技能包 `oracle-craft` 嵌入判据相关角色；技能包目录不能成为可变区 | 每次调用 | `skills.ROLE_SKILLS`、`project_profile` | — | `test_skills`、`test_oracle_protection` |

## 四、证据与可观测性

| 能力 | 入口 | 实现 | 决策 | 测试 |
|---|---|---|---|---|
| 追溯报告：需求 → 故事 → 验收条件 → 测试 → 真实命令结果 → 提交，含成本节 | `report` | `change_report` | [0001](decisions/0001-external-loop-owns-verdict.md) | `test_change_report` |
| design.md、decisions.md、活规格并入与取代记录；事后补生成 | 完成时、`document` | `openspec_docs` | [0007](decisions/0007-change-dir-never-archived.md) | `test_openspec_docs` |
| 调用账本（墙钟、轮数、token、费用）与每次调用的诊断目录 | `status`、`log` | `telemetry`、`call_log`、`change_log` | — | `test_telemetry`、`test_call_diagnostics` |
| 每个停点与报错附带下一步命令 | 所有命令 | `next_steps` | — | `test_call_diagnostics` |

## 五、系统冒烟与 CI

| 能力 | 入口 | 实现 | 决策 | 测试 |
|---|---|---|---|---|
| 项目级系统冒烟：跨变更累积、受保护、每轮尝试都跑 | 自动 | `system_smoke.run_system_smoke_isolated` | [0015](decisions/0015-test-layers.md) | `test_system_smoke` |
| 合并后提议并入系统冒烟，人审核后隔离跑绿才提交 | `smoke-propose`、`smoke-apply` | `system_smoke.propose_smoke_update`、`apply_smoke_update` | [0015](decisions/0015-test-layers.md) | `test_smoke_maintenance` |
| CI 中立入口：隔离运行变更验收套件与系统冒烟 | `ci` | `smoke_runner.run_ci` | — | `test_ci_runner`、`test_ci_path_safety` |

## 六、代理接入

| 能力 | 入口 | 实现 | 决策 | 测试 |
|---|---|---|---|---|
| 三种适配器：codex（默认）、command、manual | `.storyloop/config.json` 的 `adapter` | `project_agents` | — | `test_project_agents`、`test_codex_adapter` |
| 角色返回结构由 JSON Schema 原生约束，不符时同一会话修复 | 每次调用 | `role_schemas`、`project_agents._codex` | — | `test_role_schemas`、`test_structured_output` |
| Claude Code 适配器：按角色收窄工具、续会话、review 多视角、implement 分通道 | `agent_command` | `examples/claude-code/claude_agent.py` | [0016](decisions/0016-roles-by-authority.md) | `test_claude_adapter` |
| 进程树回收：Windows Job Object、Linux subreaper，失败即 fail-closed | 所有子进程 | `processutil`、`windows_job`、`linux_job`、`linux_supervisor` | — | `test_process_containment` |

## 七、尚不具备

见 [路线图](roadmap.md)。当前明确没有：多变更并行、开 PR / push、浏览器与数据库编排、按栈生成的执行器模板、规格与代码的漂移检查、按变更的成本上限。
