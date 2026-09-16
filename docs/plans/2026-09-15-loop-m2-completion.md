# StoryLoop M2 Completion Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Complete the remaining stage-oriented M2 Loop Runner capabilities from the approved AI Loop design without breaking the v1 workflow.

**Architecture:** Keep the verified single-Stage lifecycle as the trust boundary. Add a project-level coordinator that consumes the validated DAG, persists only bounded cross-Stage handoffs, runs final Judges after every selected Stage is green, and exposes the workflow through a separate v2 CLI surface. OpenSpec and constitution gates remain read-only inputs; Git automation is an opt-in adapter tested only in isolated repositories.

**Tech Stack:** Python 3.11+ standard library, strict JSON protocols, argv-only subprocesses, unittest, Mermaid documentation.

---

## Non-negotiable constraints

- Preserve every v1 command and state contract.
- No shell interpolation, dependency installation, network access, credential forwarding, production database access, push, or deployment.
- Agent output never creates a verdict; only configured Judge evidence can advance state.
- Payloads, stdout/stderr, file contents, and secrets are never persisted in coordinator state or run evidence.
- All new production files stay under 500 lines and functions under 50 lines.
- Every behavior change follows RED → GREEN → refactor and ends with full regression plus independent review.

## Task 8: Persistent Stage DAG coordinator and bounded handoff

**Files:** create `storyloop/stage_coordinator.py`, `storyloop/coordinator_state.py`, `tests/test_stage_coordinator.py`.

1. Write failing tests for dependency order, target closure, resume after completed Stage, blocked dependency, and changed config/payload identity.
2. Implement a versioned project-run state outside the Agent write tree, protected by an OS lock and bounded atomic JSON.
3. Execute only dependency-ready Stages through `run_stage_lifecycle`; never run dependents after a non-green predecessor.
4. Persist a bounded handoff containing Stage ID, result digest, change paths/kinds, and Judge IDs/outcomes—never raw Agent text or payload.
5. Inject ordered upstream handoffs into downstream in-memory payloads and verify resume cannot skip dependencies.
6. Run focused and full tests.

## Task 9: Final Judge phase and project verdict

**Files:** create `storyloop/final_judges.py`, modify `storyloop/stage_coordinator.py`, `storyloop/stage_result.py`, test `tests/test_final_judges.py`.

1. Write failing tests proving final Judges run only after all selected Stages are green, in declaration order, with no Agent invocation.
2. Execute configured `phase=final` Judges read-only through the existing Judge contract and shared project deadline.
3. Persist bounded final evidence bound to config and completed Stage result digests.
4. Short-circuit FAIL/ERROR/SKIP and invalidate stale final evidence when any Stage/config identity changes.
5. Run focused and full tests.

## Task 10: Replan loop and failure-evidence feedback

**Files:** create `storyloop/replan.py`, modify `storyloop/stage_lifecycle.py`, `storyloop/stage_state.py`, tests `tests/test_replan.py`.

1. Write failing tests for threshold-triggered replan, bounded machine evidence, one replan per exhausted batch, and architecture/input propagation.
2. Build a safe replan payload from failure code/fingerprint/Judge identifiers plus upstream handoff; exclude raw output and secrets.
3. Invoke the selected adapter with role `replan`; a successful response may reset only the retry batch marker, never consumed time or total attempts.
4. Persist replan receipt identity crash-safely and prevent duplicate consumption.
5. Run focused and full tests.

## Task 11: Auditable run artifacts and metrics

**Files:** create `storyloop/run_artifacts.py`, modify coordinator/lifecycle/Judge integration, tests `tests/test_run_artifacts.py`.

1. Write failing tests for deterministic `runs/<stage>/attempt-N` layout, bounded JSON evidence, atomic writes, red/green/final separation, and redaction.
2. Persist only protocol records, timestamps, durations, adapter ID, hashes, status, changes, and Judge evidence.
3. Record comparable attempt count/duration/status metrics across adapters without token or secret assumptions.
4. Treat artifact tampering as stale/blocked; artifacts cannot substitute for authoritative state.
5. Run focused and full tests.

## Task 12: OpenSpec and dual-constitution gates

**Files:** create `storyloop/spec_gate.py`, `storyloop/constitution.py`, tests `tests/test_spec_gate.py`, `tests/test_constitution.py`.

1. Write failing tests for EARS AC IDs, AC-to-test completeness, duplicate/missing mappings, bounded Markdown, and path safety.
2. Validate proposal structure without executing or rewriting specification content.
3. Generate `AGENTS.md` and `CLAUDE.md` from one bounded L1 source using atomic writes; verify byte-identical governed content.
4. Bind both generated files and proposal digests into the coordinator input identity; mismatches block execution.
5. Run focused and full tests.

## Task 13: v2 CLI and configuration loading

**Files:** create `storyloop/v2_cli.py`, `storyloop/loop_config_io.py`, modify `storyloop/cli.py`, tests `tests/test_v2_cli.py`.

1. Write failing tests for `stage-run`, `stage-status`, `stage-resume`, `--stage`, `--agent`, JSON output, and honest exit codes.
2. Load a strict UTF-8, bounded JSON v2 config (a `.yaml` file is accepted only when its content is JSON-compatible YAML, preserving zero runtime dependencies).
3. Route runtime adapter precedence through the existing selector and expose waiting/unsafe recovery flags without weakening acknowledgement checks.
4. Keep all v1 parser and dispatch behavior unchanged.
5. Run focused and full tests.

## Task 14: Optional Git discipline, demo, and release documentation

**Files:** create `storyloop/git_workflow.py`, `tests/test_git_workflow.py`, `examples/v2-demo/`; modify `README.md`, `docs/architecture.md`, `docs/protocols.md`.

1. Write failing tests in temporary Git repositories for clean-tree checks, exact feature branch validation, no-ff merge argv, conflict fail-closed behavior, and no push.
2. Implement an opt-in argv-only Git adapter; never initialize, commit, merge, or push unless called explicitly by a host application.
3. Add a deterministic two-command-adapter v2 demo proving DAG → RED/Agent/L1/L2 → final evidence with comparable artifact layout.
4. Document v1/v2 migration, recovery, security limits, OpenSpec gate, constitution generation, and Mermaid end-to-end flow.
5. Run the demo, all tests, compile checks, size/security scans, and independent code review; update this plan with final evidence.

## Completion report

The final report must distinguish repository-complete M2 capabilities from out-of-repository M1 Java/Admin/Android starter construction and M3/M4 business rollout, which require separate repositories and organizational inputs.

## Final evidence (2026-09-15)

- Task 8-14 已全部实现；v1 CLI 与状态协议保持兼容，v2 通过独立 stage-* 命令暴露。
- Coordinator 按 DAG 依赖闭包运行，handoff 有界且由权威 Stage 状态重新推导；仅篡改 run.json 不能伪造 GREEN 或跳过依赖。
- Final Judges 只在选定 Stage 全绿后运行，共享项目级单调时钟期限，不调用 Agent。
- Replan 回执 crash-safe、一次性消费；失败机器证据与上游 handoff 均传入下一轮，预算不重置。
- Attempt/final 审计投影通过 artifact_chain / final_artifact_digest 绑定回权威状态；未绑定崩溃缺口可恢复，已绑定历史的删除、变化或额外目录 fail-closed，状态查询同样验证。
- OpenSpec、L1 双宪法与配置读取默认 fail-closed，并逐级拒绝父目录 symlink/junction/reparse 逃逸；兼容模式只允许可信 Python 宿主显式选择。
- 可选 Git adapter 仅使用 argv、禁用 hooks、不 push；冲突保持阻断。
- python -m unittest discover -s tests -p test_*.py -q：228 tests OK，2 skipped（当前 Windows 权限不允许创建测试所需的 symbolic/directory link）。
- python -m compileall -q storyloop tests examples/v2-demo：通过。
- AST 规模门禁：所有生产文件不超过 500 行，所有函数不超过 50 行。
- 安全静态扫描：无 shell=True、os.system、裸 except、私钥/API key 特征或动态 eval/exec。
- python examples/v2-demo/run_demo.py：agent-a、agent-b 均为 PROJECT_GREEN，各 1 次 attempt、1 个 final Judge。
- 独立复审：Critical 0，Important 0，结论 Ready。
- 当前工作区没有 .git 元数据，因此无法生成提交记录；未执行 commit、push、merge 或部署。