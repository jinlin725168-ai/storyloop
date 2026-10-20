# 测试与验收基础设施

对应引擎版本：0.1.0，核对日期 2026-10-16（子项目 2a 工作分支）。这一页讲引擎**自己的**测试；目标仓库的测试分层见 [概念手册](concepts.md) 与 [接入手册](onboarding-a-repository.md)。

## 一、单元与端到端测试

- 框架：标准库 `unittest`，不依赖第三方包、模型账号或网络。
- 位置：`tests/`；文件与用例数量随开发变化，以全量运行输出为准。本机（WSL2）全量通常超过 10 分钟。
- 原理：每个测试在临时目录建一个真实 Git 仓库，`init` 锁底座，用脚本化的 `LoopServices`（Python 函数代替代理）或假的 `codex` / `claude` 可执行文件驱动闭环，断言状态、文件和 Git 历史。

运行：

```bash
.venv/bin/python tests/run.py --suite full          # 全量：每个模块一个进程，按 CPU 核数并行
.venv/bin/python tests/run.py --suite core          # 核心套件（CI 在每个 PR 上跑的那部分）
.venv/bin/python tests/run.py --suite core --list   # 只列出模块
.venv/bin/python -m unittest tests.test_project_loop tests.test_smoke_repair   # 指定模块
```

`unittest discover` 仍可用，但串行跑全量要一小时左右（Linux，约 870 个测试）；`run.py` 的墙钟时间由最慢的单个模块决定（目前是 `test_routing_e2e`，约 8 分钟）。

### 测试套件

套件定义在 `tests/suites.py`：

| 套件 | 内容 | 什么时候跑 |
|---|---|---|
| `core` | 主流程端到端（`test_product_e2e`、`test_nature_e2e`、`test_reports_e2e`、`test_roles_e2e`、`test_project_loop`）、安全边界、命令行、跨平台回归（短路径、autocrlf、UTF-8 管道），以及每个都在几秒内跑完的纯逻辑模块 | 每个 PR、每次 push 到 main；Linux 4 路并行约 1 分钟 |
| `full` | `tests/test_*.py` 全部模块 | CI 每天定时一次、手动触发；本地合并前、按计划实现时 |

新增的测试模块默认只在 `full` 里；作者认为它守护的是主流程或安全边界时，在 `suites.py` 对应分组里加上它，`test_suites` 会检查名单里的模块都存在。分流、设计批准等重型流程的模块（单个就要几分钟）只在 `full` 里，它们的回归最晚在第二天的定时全量里暴露。

测试会提交到临时仓库，所以本机需要 git 身份（`user.name`、`user.email`）。

### 共用夹具

| 夹具 | 位置 | 用途 |
|---|---|---|
| `product()` | `tests/support.py` | 一个合法的单故事产品合同 |
| `repository(root)`、`git(root, ...)` | `tests/test_baseline_behavior.py` | 建带 CI 文件的临时 Git 仓库、跑 git 命令 |
| `smoke_payload(...)` | `tests/test_project_loop.py` | 一个能在候选副本里跑的变更验收套件 |


### 测试地图

| 主题 | 测试文件 |
|---|---|
| 端到端样板 | `test_product_e2e`（干净仓库 → 合同 → RED → 实现 → 报告 → no-ff 合并，CI 文件字节不变） |
| 底座与探测 | `test_baseline_*`、`test_project_profile*`、`test_monorepo_detection`、`test_base_propose`、`test_base_upgrade`、`test_resume_after_base_upgrade` |
| 合同与规格 | `test_changes_*`、`test_change_approval`、`test_product_spec`、`test_spec_gates`、`test_openspec_docs` |
| 闭环与停点 | `test_project_loop*`、`test_agent_unavailable`、`test_design_role`、`test_characterization`、`test_test_layers`、`test_answers`、`test_manual_acceptance`、`test_run_reset`、`test_smoke_repair`、`test_completion_resilience` |
| 验收套件与冒烟 | `test_smoke`、`test_acceptance_layout`、`test_system_smoke`、`test_smoke_maintenance`、`test_ci_runner`、`test_candidate_allow` |
| 安全与边界 | `test_boundaries`、`test_project_boundaries`、`test_project_security_gates`、`test_project_loop_tampering`、`test_workspace_scratch`、`test_ci_path_safety`、`test_windows_path_guards`、`test_process_containment` |
| 判据与性质 | `test_change_nature`、`test_oracle_counts`、`test_oracle_tiers`、`test_oracle_baseline`、`test_oracle_protection`、`test_nature_e2e` |
| 代理与适配器 | `test_project_agents`、`test_codex_adapter`、`test_claude_adapter`、`test_role_schemas`、`test_structured_output` |
| 纠偏 | `test_failure_attribution`、`test_failure_routing`、`test_no_progress`、`test_facts`、`test_stop_report`、`test_supersede`、`test_semantic_repair` |
| 可观测性 | `test_telemetry`、`test_call_diagnostics`、`test_change_report` |
| 命令面与技能 | `test_cli`、`test_product_cli`、`test_core`、`test_skills`、`test_foundation_survey` |

## 二、CI

`.github/workflows/ci.yml`，push 到 main、PR、每天定时（UTC 19:00）与手动触发：

| 作业 | 内容 |
|---|---|
| 改动范围 | 判断是否只改了文档（`docs/`、`LICENSE`、`storyloop/` 与 `tests/` 以外的 `.md`）；是则跳过测试 |
| 测试（矩阵） | Ubuntu、Windows，Python 统一 3.11（pyproject 声明的最低版本），全部必须通过；步骤为 `compileall`（含 `examples/claude-code`）→ `pip install .`（确认打包与技能包数据）→ `tests/run.py`：PR 与 push 跑 `core`，定时跑 `full`，手动触发可选（默认 `full`），4 路并行 |
| 密钥扫描 | gitleaks（固定版本、校验摘要）扫描本次引入的每个提交，手动触发时扫描全部历史；只改文档时也运行 |
| CI 结果 | 汇总以上作业，跳过算通过；`main` 的分支规则只要求这一项 |

## 三、真实模型验证

单元测试不调用模型。真实闭环在目标仓库上跑，记录在 [案例与成本参考](case-studies.md)；入门教程（[tutorial.md](tutorial.md)）用 `examples/todo-demo/` 可在本机复现一次。

子项目 2a 首轮 [Q8 验收](superpowers/specs/2026-10-14-子项目2a-纠偏-design.md#13-实现差异与验收状态) 已获授权执行，使用隔离 todo-demo 与原生 Codex。实际通过路径为 `NO_PROGRESS` → `supersede --reason` → 新变更 PASSED，以及修复实现提示后重构 PASSED；该轮未触发原定 `NEEDS_SPEC_FIX` 与 `ORACLE_DEFECT`；随后定位到功能红层跳过 review 的问题并修复，补验见下。

31 次角色调用中有 3 次失败（2 次 Claude 订阅禁用、1 次原生 Codex 停滞）；旧合同还实际触发 product、acceptance 各一次语义补正。Codex 未报告费用，账本 `cost_usd` 为 null，金额未知。两个通过变更各有 9 项单元测试、6 项验收通过；integration、smoke、checks 未配置，均 SKIPPED。

2026-10-16 自动测试记录：此前 `.venv/bin/python -m unittest discover -s tests -t .` 运行 405 项、890.334 秒，`OK (skipped=1)`，审查修复后的 92 项定点回归通过。本轮加入真实验收修复后，全量 406 项、894.176 秒，1 ERROR、1 skipped：`test_an_extend_lock_cannot_mask_a_deleted_test` 触发 ignored-file 保护；单测和有限前序组合复现均通过，根因尚未定位。最终通过仓库外诊断包装运行同一全量：`/home/jinlin/work/storyloop/.venv/bin/python /tmp/storyloop-q8-diagnostic-runner.py`，407 项、899.306 秒，`OK (skipped=1)`。包装仅在 ignored-file 异常时补充路径和 Git 状态，不改变引擎判断；输出中的拦截记录均为安全测试预期行为。前一次非预期错误未复现，不据此宣称已定位或修复其根因。

Q8 补验在新的隔离 todo-demo 上进行：初始产品合同与套件来自上轮真实产出的回放，判据场景显式注入 Git 缺失时提前 return 的缺陷，对照实验验证该缺陷可让空实现误过；不计为模型自然生成缺陷。特征化、实现、审查调用真实原生 Codex。两个场景首轮分别达到 `NEEDS_SPEC_FIX` 与 `ORACLE_DEFECT`，并继续标准修订与判据修复流程。自动测试新增功能红层分流、review pass 不覆盖失败、纯环境跳过审查、混合发现与驳回、问答中断连续失败及只读保护。

补验最终结果：合同经 `supersede` 后的新变更首轮 PASSED（unit 8、acceptance 5）；判据经 `acceptance-repair` 后新 epoch 首轮 PASSED（unit 11、acceptance 6）。integration、smoke、checks 未配置，均 SKIPPED。新增 14 次真实角色调用、0 次代理失败，费用金额未知。普通全量 `python -m unittest discover -s tests -t .`：416 项、971.629 秒，`OK (skipped=1)`；全量启动后只有恢复提示文字另改，26 项 CLI/停止报告回归通过，补丁及提示文案均独立审查通过。偶发 Git 可见性错误未再次出现，新增路径诊断但根因仍未证明。

继续排查（2026-10-16）：当前 `ff9a8eb` 的目标单测带快照诊断连续运行 40 次全部通过；另从首次失败版本 `a7c1b84` 导出独立源码副本，按原发现顺序运行截至目标测试的全部 209 项，395.732 秒，`OK (skipped=1)`。因此未找到稳定触发条件，也未证明前序测试污染；原始错误没有保存不可见路径，临时测试仓库已清理，无法仅凭旧堆栈唯一归因。本次没有做猜测性源码修改，缺陷继续保留为未定位。

## 四、会被跳过的测试

| 测试 | 条件 | 原因 |
|---|---|---|
| `test_process_containment` 的 subreaper 用例 | 非 Linux | 依赖 `PR_SET_CHILD_SUBREAPER` |
| `test_boundaries` 的句柄语义用例 | 非 Windows | Windows 专有行为 |
| `test_boundaries` 的硬链接用例 | 文件系统不支持硬链接 | 运行时 `skipTest` |
| `test_spec_gates` 的假 `openspec` 用例 | Windows | 假 CLI 是 POSIX 脚本 |

## 五、坑

- 测试默认分支名不能假定为 `master`；CI 设置了 `init.defaultBranch main`，本地也建议设置。
- Windows 上 `autocrlf` 会改变检出字节：套件指纹按 LF 规范化，新增指纹逻辑也要这样做。
- Python 3.12 的 `unittest` 在没有用例时退出码为 5：夹具仓库里要放至少一个单元测试。
