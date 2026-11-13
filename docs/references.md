# 研究来源与设计取舍

调研日期：2026-09-12。以下是方法与机制参考；本工程独立实现，没有复制它们的源码或宣称兼容其全部工作流。

- [Cucumber Example Mapping](https://cucumber.io/docs/bdd/example-mapping/)：用故事、规则、例子和问题共同澄清产品。StoryLoop 对话产物保留这四类信息。
- [Cucumber Three Amigos](https://cucumber.io/docs/bdd/who-does-what/)：产品、QA 和工程各自补充视角；业务取舍由人决定。
- [BMAD 需求与规格](https://docs.bmad-method.org/plan/define-requirements-and-a-specification/)：从自由表达开始的产品协作。
- [BMAD 自治循环](https://docs.bmad-method.org/build/autonomous-development-loops/) / [bmad-loop](https://github.com/bmad-code-org/bmad-loop)：单故事实施、独立审查、有限重试与持久化状态。
- [TEA ATDD](https://bmad-code-org.github.io/bmad-method-test-architecture-enterprise/how-to/workflows/run-atdd/)：风险驱动验收设计。StoryLoop 只有执行到目标行为断言失败才标 RED。
- [bmad-loop TEA 集成](https://github.com/bmad-code-org/bmad-loop/blob/main/docs/tea-plugin-guide.md)：当前集成存在开发后运行 ATDD 与建议式门禁。StoryLoop 将验收前移，并对缺失或无效证据拒绝通过。
- [GitHub Spec Kit](https://github.github.com/spec-kit/reference/agentic-sdd.html)：先检查需求完整性和一致性，再拆解与实施。
- [OpenSpec 工作流](https://github.com/Fission-AI/OpenSpec/blob/main/docs/workflows.md)：显式组织规格与变更，适合持续演进；StoryLoop 以批准基线和过期证据处理变化。
- [Superpowers TDD](https://github.com/obra/superpowers/blob/main/skills/test-driven-development/SKILL.md)：实际观察红、绿与重构的开发纪律；区分提示词规范与机器强制。
- [Anthropic 长任务 Harness](https://www.anthropic.com/engineering/harness-design-long-running-apps)：生成与独立评估分工、实际操作产品和明确评价标准。
- When 项目的 Loop 工程：参考外部 Judge、失败证据回填、状态恢复和保护路径思路。此工程不依赖 When 业务模块或课程编号。

## HelixHarness（2026-11-08）

[didi/helixharness](https://github.com/didi/helixharness)（基线 `8b23069`）：面向 Codex/Claude Code 的需求→实现→测试→审查→归档插件，以及评测 harness 版本的平台。调研全文与补充分析见 [research/2026-11-08-HelixHarness调研.md](research/2026-11-08-HelixHarness调研.md)，规划见[验证入口及评测底座设计](superpowers/specs/2026-11-08-验证入口与评测底座-design.md)。StoryLoop 独立实现，没有引入其工作流或平台服务。

| 借鉴点 | HelixHarness 出处 | StoryLoop 落地 |
|---|---|---|
| seam 在规格中预先确认，只在 seam 上测 | `helixharness/skills/tdd/SKILL.md`、`tdd/tests.md` | 合同必填 `verification_entries`，三方核对（#106）；“准备 / 执行 / 观察”（#122）；入口覆盖（#123） |
| 审查意见稳定 ID 与 MUST_FIX / DECISION_NEEDED / ADVISORY | `helixharness/skills/code-review/SKILL.md` | 审查分级与同一 AC 收敛（#107） |
| 冻结 Case、Base/Solution 上校准 F2P/P2P、缺证据不可比较 | `helixharness-platform/bench/scenario_calibration.py`、`bench/docs/concepts.md` | `storyloop bench`（#105、#113），见 [bench.md](bench.md) |
| 资源核算不完整只标 PARTIAL、不阻断评分 | `helixharness-platform/bench/resource_accounting.py` | 缺美元成本记 `UNAVAILABLE`（#114） |
| 有上限的自动返工 | `helixharness-platform/bench/workflow_goal_control.py` | 模拟人一次纠正（#115）；确定性验收缺陷自动退回（#121） |
| 按证据区分环境失败与产物缺陷 | `helixharness-platform/bench/hard_gate_analysis.py`、`scenario_diagnostics.py` | harness 用例诊断（#116）、RED 分类（#120）、`regression_updates`（#117） |
| 语义评审限定输入与结构化输出 | `helixharness-platform/bench/semantic_judging.py`、`scenario_judging.py` | RED 前判据强度预审（#124） |
| 决策树、影响分析、系统化排错等技能 | `helixharness/skills/*`、`workflows/single/propose.md` | `spec-craft` 决策树、`tdd`、`impact-analysis`、`systematic-debugging`（#110） |
| 知识与规范分层、精确 diff 授权 | `helixharness/commands/init.md`、`helixharness/workflows/single/archive.md` | survey 分节与 `foundation-use`（#111）、`knowledge-propose` / `authorize-knowledge`（#112） |

未采用：用其插件主链替换 StoryLoop 外环（会削弱受保护的盲写验收、实现前 RED 和边界审计）；整套平台服务与评分权重；把每个设计树分支都升级为人工审批；按需加载知识索引（延至阶段②）；多仓 DAG（待单仓多故事通过后再评估）。

