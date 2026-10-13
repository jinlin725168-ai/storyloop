# 测试与验收基础设施

对应引擎版本：0.1.0，核对日期 2026-10-11。这一页讲引擎**自己的**测试；目标仓库的测试分层见 [概念手册](concepts.md) 与 [接入手册](onboarding-a-repository.md)。

## 一、单元与端到端测试

- 框架：标准库 `unittest`，不依赖第三方包、模型账号或网络。
- 位置：`tests/`，63 个文件、272 个测试用例；本机（WSL2）全量约 10 分钟。
- 原理：每个测试在临时目录建一个真实 Git 仓库，`init` 锁底座，用脚本化的 `LoopServices`（Python 函数代替代理）或假的 `codex` / `claude` 可执行文件驱动闭环，断言状态、文件和 Git 历史。

运行：

```bash
.venv/bin/python -m unittest discover -s tests -t .          # 全量
.venv/bin/python -m unittest tests.test_project_loop tests.test_smoke_repair   # 指定模块
```

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
| 可观测性 | `test_telemetry`、`test_call_diagnostics`、`test_change_report` |
| 命令面与技能 | `test_cli`、`test_product_cli`、`test_core`、`test_skills`、`test_foundation_survey` |

## 二、CI

`.github/workflows/ci.yml`，push 到 main、PR 与手动触发：

| 作业 | 内容 |
|---|---|
| 测试（矩阵） | Ubuntu + Python 3.11、Ubuntu + Python 3.12、Windows + Python 3.12，全部必须通过；步骤为 `compileall`（含 `examples/claude-code`）→ `pip install .`（确认打包与技能包数据）→ 全量 unittest |
| 密钥扫描 | gitleaks 扫描整个工作树 |

## 三、真实模型验证

单元测试不调用模型。真实闭环在目标仓库上跑，记录在 [案例与成本参考](case-studies.md)；入门教程（[tutorial.md](tutorial.md)）用 `examples/todo-demo/` 可在本机复现一次。

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
