# HelixHarness 调研：对 StoryLoop 的可用启发

> 本文由 Codex 于 2026-11-08 完成，原放在共享工作区的 `research_notes/`，2026-11-13 收入知识库。文末“补充分析”是人与监工复核后的结论，规划以[验证入口及评测底座设计](../superpowers/specs/2026-11-08-验证入口与评测底座-design.md)为准；落地情况见[研究来源](../references.md#helixharness2026-11-08)。

> 静态代码调研，2026-11-08。HelixHarness 基线：`didi/helixharness` 的 `8b23069fb464104ce556798ec69737f7eba469b3`（2026-11-04）；StoryLoop 基线：本地 `origin/main` 的 `926bcdde97554119be304f15838e148cdfd9f12f`。未运行 HelixHarness 代码、安装依赖或验证其运行效果。下列路径均相对各自仓库根目录；StoryLoop 引用的是 `origin/main` 内容，不是可能滞后的本地 `main`。

## 结论先行

最值得借鉴的是**把验收夹具在已知 Base/Solution 上预校准**、**把同一判据的审查意见一次收全并稳定编号**，以及**按冻结 Case 对比框架版本和人工成本**。HelixHarness 的插件主链路没有 StoryLoop 的实现前独立验收套件、外部 RED 判决和受保护判据，因此不宜替换 StoryLoop 的裁判外环。证据：HelixHarness `helixharness/workflows/single/apply.md:90-116`、`helixharness-platform/bench/scenario_calibration.py:206-253`；StoryLoop `docs/architecture.md:13-28,39-45`。

## 对象与架构

选定 [滴滴官方仓库 didi/helixharness](https://github.com/didi/helixharness)：仓库自述明确针对 Codex/Claude Code 的需求→实现→测试→审查→归档，并由滴滴金融团队维护；GitHub 页面查询时约 329 stars、55 forks，提交日期为 2026-11-04。搜索中的 `HKUDS/HELIX` 是模型与 harness 共同优化研究，`strands-agents/harness-sdk` 是通用代理 SDK，均非同名且同定位的项目。活跃度只作为选仓辅助，不当作质量证据。证据：HelixHarness `README.md` 的项目定位、仓库地图、版本与许可证；`docs/workflow-architecture.md:3-20`；[GitHub 仓库页](https://github.com/didi/helixharness)。

HelixHarness 分两层：`helixharness/` 是可独立工作的 Lite 插件，九个公共命令经 Router 进入 Single/Workspace 的 Propose→Apply→Archive；`helixharness-platform/` 是可选的使用观测、冻结版本评测与框架演进平台，**评测对象是 harness 版本，不是普通业务代码交付质量服务**。宿主 Host API 1.1 用 manifest/Host Kit 隔离 Codex Skill 投影与 Claude 原生命令、hook、子代理和遥测；当前只支持这两者。证据：`docs/workflow-architecture.md:6-39`、`helixharness/reference/host-api.md:1-40,42-102`、`README.md`“A platform for iterating on HelixHarness”。

插件流程：Propose 先调查仓库事实、按决策树整批询问当前可决定的问题，用户确认共同理解后生成 Spec/Tasks；Apply 绑定 Spec 收据与工作区基线，逐任务 TDD、最终全套测试、并行 Standards/Spec 双轴只读审查，再写含 AC→Task→Result 和 RED/GREEN 记录的 Evidence；Archive 验证收据与范围、归档并询问精确 commit/push 选择。Workspace 按仓库依赖 DAG 分批派发完整 Single 流程，聚合跨仓合同；不直接在根仓制造第二份验收。证据：`helixharness/workflows/single/propose.md:148-180,200-272`、`helixharness/workflows/single/apply.md:47-86,90-116,137-157`、`helixharness/templates/verify-report.md.tpl:24-64`、`helixharness/workflows/single/archive.md:175-233`、`helixharness/workflows/workspace/apply.md:7-64`。

## 逐项对照

| 维度 | HelixHarness | StoryLoop `origin/main`；判断 |
| --- | --- | --- |
| 需求与规格 | 决策树一次展示全部当前 frontier，事实由代理查、决策由人答；Spec 的 AC/Testing Decisions 再派生垂直任务。`helixharness/workflows/single/propose.md:150-180,236-261` | 产品合同含 EARS、Given/When/Then、行类型和批准指纹；需求与验收分权更强。可借其“整批展示、只问真实决策”降人工，但完整设计树可能增加前期问答。`docs/concepts.md`“产品合同”“行类型”；`docs/architecture.md:13-16,39-42` |
| 判据与 RED | 插件 TDD 要求逐任务先红后绿并把结果写 Evidence；语义校验器只查任务结构，不判断 AC/夹具语义。平台另有 Case Gold 的 F2P/P2P Base/Solution 校准。`helixharness/workflows/single/apply.md:90-104`、`helixharness/workflows/single/apply.md:47-54`、`helixharness-platform/bench/scenarios.py:292-318`、`helixharness-platform/bench/scenario_calibration.py:206-253` | 独立 acceptance 先于 implement 盲写、锁定并在候选副本观察 RED，特征化旧行为、系统冒烟和计数防空跑更强；但夹具前提猜错导致反复修复，RED 不能单独证明失败机制正确。`docs/concepts.md`“验收级 RED”“特征化测试”“系统冒烟”“判据自证”；`storyloop/smoke_runner.py:93-150`；dogfood 第 27-28、35-41、46 条 |
| 实现与审查 | 同一冻结 Review Bundle 由两个隔离只读子代理并行审 Standards/Spec；finding 分 MUST_FIX、DECISION_NEEDED、ADVISORY，稳定 ID；无进展时停止。`helixharness/skills/code-review/SKILL.md:18-39,89-138` | 外环先做边界审计及分层测试再审查，审查代理不拥有裁判权；对判据强度的逐层追问仍可能循环。StoryLoop 的证据可信边界更强，Helix 的稳定 ID/非阻断分级和整批意见更利于收敛。`docs/architecture.md:19-28,39-45`；dogfood 第 40、43、46-47 条 |
| 停点、恢复、人机交互 | Apply state 持久化 Spec/工作区基线摘要；旧 Evidence 无效时重做测试与审查，不伪造 APPROVE；既有脏文件只在同一路径发生重叠时请求决定。Archive 对当前精确范围询问提交/推送。`helixharness/workflows/single/apply.md:56-82`、`helixharness/workflows/single/archive.md:175-201` | 运行状态原子落盘，合同/验收/人工验收绑定指纹，阶段可幂等恢复；但 dogfood 中可修复的夹具/规划问题仍多次停人。`docs/architecture.md:31-47`；dogfood 第 39、42、46 条 |
| 安全与隔离 | 插件精确 Review Scope、用户旧改动基线、未知宿主 fail closed；平台校准在临时 Base/Solution 副本运行，检查路径和预算。插件 Apply 在当前 checkout 工作，隔离性依赖宿主和脚本规则；未见等价的实现前受保护验收副本。`helixharness/scripts/workflow.py:935-1064`、`helixharness/reference/host-api.md:86-102`、`helixharness-platform/bench/scenario_calibration.py:144-230` | 候选副本、可变区/底座/判据边界审计、环境变量白名单和进程回收更适合裁判角色；文档也明确不是 OS 文件系统沙箱。`docs/architecture.md:39-59` |
| 遥测、报告、指标 | 统计默认关闭，授权后分阶段记账；平台以冻结 Case/模型/策略做 Base/Candidate 评测，区分业务硬门、工作流质量、效率、不可比较证据。`helixharness/reference/telemetry-command-protocol.md:1-9`、`helixharness-platform/bench/docs/concepts.md:5-58` | 已有 `metrics` 的首过、返工、介入等计算，但路线图阶段 2 尚要求自动复现 dogfood 表；宜复用“冻结同题、硬门先行、缺失保持不可用”的评测方法。`storyloop/metrics_compute.py:38-56,121-148,294-320`；`docs/roadmap.md:54-59` |
| 扩展 | Host Kit 用 manifest/registry 和专项测试接新宿主；Workspace 有仓库所有权、DAG 和完成聚合；上下文由 harness/knowledge 索引按任务加载。`helixharness/reference/host-api.md:42-118`、`helixharness/workflows/workspace/apply.md:20-56`、`helixharness/reference/loading-protocol.md:1-30` | StoryLoop 有 CLI 角色适配器与基础知识库，现阶段目标仍是单仓真实多故事；跨仓平台先行投入不合路线图。`docs/concepts.md`“七个角色”“底座知识库”；`docs/roadmap.md:54-59,86` |

## 可直接拿来用的素材

> 许可证段落为调研时的记录。2026-11-08 人决定：项目处于雏形、未商用，后续可能完全开源，许可证暂不作为取舍因素；实际复制上游文本时仍保留其版权与许可声明。

以下指**复制为待适配素材**，不是原样接入 StoryLoop 引擎。HelixHarness 原创代码/模板/合成示例是 **Apache License 2.0**；StoryLoop 是 **Apache-2.0 + Commons Clause License Condition v1.0**。Apache-2.0 允许复制、修改和再分发，原则上可放入 StoryLoop 的附加限制分发物，但复制部分仍须保留 Apache 授权、版权、改动说明和 HelixHarness `NOTICE`；不可把上游原始授权误标成只有 StoryLoop 条款。若分发/商业授权方案有争议，落地前做法律复核。Helix 的 TDD 与 Code Review 技能有 `mattpocock/skills` **MIT** 改编内容，复制时还需保留 MIT 版权/许可；平台静态资源的 HTMX 是 0BSD、Tailwind CSS 是 MIT，均需另核告知。证据：HelixHarness `LICENSE:67-115`、`NOTICE:1-5`、`THIRD_PARTY_NOTICES.md:1-13`、`helixharness/THIRD_PARTY_NOTICES.md:65-80`、`helixharness-platform/THIRD_PARTY_NOTICES.md:1-18`；StoryLoop `LICENSE:1-23`。

| 素材 | 依据与收益 | 成本 / 风险 |
| --- | --- | --- |
| `examples/starter/requirements/task-filter.md`、`examples/verify_feature.py`、`helixharness-platform/bench/sample_data/task-filter/{case.json,gold.json}`：作为 StoryLoop **接入/评测示例素材** | 已有自包含需求与独立验收检查、Case/Gold 样本，可缩短阶段 2 的同题基线构造。`examples/README.md`；`helixharness-platform/bench/docs/concepts.md:5-15` | 低；必须改写成 StoryLoop 行类型、受保护验收 manifest 和本地执行器，不可把 Helix Gold 直接算 StoryLoop RED。样本仍需在 StoryLoop 中实测。许可：Helix 原创 Apache-2.0，保留许可/NOTICE。 |
| `helixharness/templates/harness/security.md.tpl` 与 `helixharness/templates/verify-report.md.tpl`：作为**可选模板字段库** | 前者覆盖身份、依赖、脱敏检查；后者明确 AC→Task→Result、TDD、验证命令、双轴审查收据。可用于阶段 4 判据/证据完整性清单。`helixharness/templates/verify-report.md.tpl:24-80`；`helixharness/templates/harness/security.md.tpl:1-50` | 低到中；StoryLoop 已有更严格报告和安全边界，需按字段映射、去重，不要复制完成判定逻辑。许可：原创 Apache-2.0，保留许可/NOTICE及改动说明。 |

## 值得参考的做法

1. **阶段 1：夹具自动预检与有界重写。** 借平台校准的“同一测试在 Base/Solution 分别执行并区分 F2P/P2P”思路，在 StoryLoop 的验收产出后先检查夹具前提、导入来源和预期失败原因；只把 `ORACLE_DEFECT` 及环境/夹具错误回喂 acceptance 1–2 次，仍由外环复证 RED。收益：减少 dogfood 第 39、46 条的人工修复；成本中；风险：自动重写不能改变合同或放宽判据，不能以“能红”替代语义正确。依据：HelixHarness `helixharness-platform/bench/scenario_calibration.py:144-253`；StoryLoop `storyloop/acceptance_repair.py:39-66,130-163`、dogfood 第 39、46 条。
2. **阶段 1/4：一次收全、稳定 ID、同一 AC 收敛。** 将 Helix 双轴 Review 的稳定 finding ID、`MUST_FIX/ADVISORY/DECISION_NEEDED` 分级，移到 StoryLoop 的方案与判据一致性审查；同一 AC 第二轮仍报强度不足时，要求 reviewer 一次给完整可执行判据规格，并核对合同/方案上限。收益：减少逐层追问和越界修复；成本中；风险：非阻断分级不能掩盖真实新行为缺测。依据：HelixHarness `helixharness/skills/code-review/SKILL.md:89-138`；dogfood 第 40、43、46-47 条。
3. **阶段 2：冻结同题评测与不可比较语义。** 用固定需求、仓库 base、判据版本、代理配置和预算对比 StoryLoop 版本；指标保留代理调用/耗时、判据修复、非例行人工、首过及硬失败，不用平均分抵消业务失败。收益：让路线图阶段 2 的 dogfood 表可复现并能解释退化；成本中到高；风险：Case/Gold 维护成本与少量样本过拟合。依据：HelixHarness `helixharness-platform/bench/docs/concepts.md:17-58`、`helixharness-platform/bench/scoring.py:108-180`；StoryLoop `docs/roadmap.md:54-59`。
4. **阶段 3：按仓库所有权的 DAG 调度留作扩面设计。** 真实多仓需求出现时参考 RepoJob 的 owner、read/write、依赖批次和子仓独立收据；先完成单仓 2–3 故事与真实模型撞底座。收益：跨仓失败可局部恢复；成本高；风险：并发、跨仓合同和合并语义显著增加。依据：HelixHarness `helixharness/workflows/workspace/apply.md:20-64`；StoryLoop `docs/roadmap.md:58,86`。

## 不建议采用

| 做法 | 原因 / 收益损失 / 成本风险 |
| --- | --- |
| 用 Helix 插件主链替换 StoryLoop 外环 | 插件 TDD/双轴审查侧重代理纪律和报告，不能替代受保护的盲写验收、实现前外部 RED、候选隔离和底座审计；迁移成本高且会削弱 StoryLoop 核心可信性。`helixharness/workflows/single/apply.md:90-116`；StoryLoop `docs/architecture.md:13-28,39-59`。 |
| 直接引入整套 `helixharness-platform/` 服务或复制其评分权重 | 其目标是 Helix 框架版本评测，部署、Case 管理、Worker、模型服务和评分都带来高维护成本；StoryLoop 阶段 2 先需可信的调用/人工基线。60/20/20 是该平台的产品选择，不是 StoryLoop 的充分性证明。`README.md`“A platform for iterating on HelixHarness”；`helixharness-platform/bench/docs/concepts.md:48-58`；StoryLoop `docs/roadmap.md:54-59`。 |
| 把每个设计树分支都升级为新人工审批 | Helix 的决策树强调所有未决选择交给用户，StoryLoop 当前目标是压低非例行介入；照搬会加重停点。只借“整批问真实决策、事实自查”。`helixharness/workflows/single/propose.md:150-180`；StoryLoop `docs/roadmap.md:54-56`、dogfood 第 42 条。 |
| 直接复制 TDD/Code Review 技能全文 | 与 StoryLoop 现有 oracle-craft/角色权限重叠，且含 MIT 上游改编内容；需合并语义和双层署名，整体接入成本中高。`helixharness/THIRD_PARTY_NOTICES.md:65-80`；StoryLoop `storyloop/skills/oracle-craft/SKILL.md`、`docs/concepts.md`“七个角色”。 |

## 优先级建议

1. **P0：验收夹具预检 + 1–2 次自动修复**，并记录修复前后失败类型；先对 dogfood 第 39、46 条复盘场景验证。
2. **P0：判据审查按 AC 汇总全部阻断项**，稳定 ID 和同一 AC 两轮收敛，修复理由受合同/已批准方案约束。
3. **P1：建立 3–5 个冻结 Case 的版本对照小集**，优先复现 dogfood 的人工次数、耗时、首过和判据缺陷；缺证据标不可比较。
4. **P2：借用少量 Apache-2.0 模板/示例字段**补 StoryLoop 接入样本与证据清单，保持许可证与 NOTICE。
5. **P3：只在阶段 3 单仓多故事通过后**再验证多仓 DAG/Host Kit 设计需求。

## 关键源码位置索引

- Helix 工作流/恢复：`docs/workflow-architecture.md:6-45`；`helixharness/scripts/workflow.py:912-1064,1313-1371,1530-1657`；`helixharness/scripts/workflow_router.py:91-266`。
- Helix 宿主/编排/上下文：`helixharness/reference/host-api.md:1-127`；`helixharness/workflows/workspace/apply.md:7-64`；`helixharness/reference/loading-protocol.md:1-30`。
- Helix 验收/评测/遥测：`helixharness/skills/code-review/SKILL.md:18-138`；`helixharness-platform/bench/scenarios.py:252-318`；`helixharness-platform/bench/scenario_calibration.py:81-253`；`helixharness-platform/bench/scoring.py:108-180`；`helixharness/reference/telemetry-command-protocol.md:1-9`。
- StoryLoop 外环：`docs/concepts.md`、`docs/architecture.md:9-68`、`docs/roadmap.md:52-86`；`storyloop/project_loop.py:65-129,267-427`；`storyloop/smoke_runner.py:93-180`；`storyloop/acceptance_repair.py:39-163`；`storyloop/metrics_compute.py:38-320`。
- 当前实测痛点：`/home/jinlin/work/storyloop-dogfood3-findings.md` 第 24–47 条，尤其第 27–28、35–41、42–47 条。

## 补充分析（2026-11-08，人与监工复核）

人认为上文没有把真正值得参考的点和 StoryLoop 自身的最大问题讲透，监工据 HelixHarness 源码和 dogfood 记录补了以下结论，并经人批准写入设计。

**StoryLoop 的最大问题**：验证入口（seam）不在合同里。acceptance 选择从哪里观察行为，design 选择改哪条路径，review 再判断两者是否一致，三个角色各自猜。dogfood7/8/11 的反复返工都源于验收调用的入口与方案改动路径错位，随后被逐层追问判据强度。HelixHarness 要求 seam 在 Spec 的 Testing Decisions 中预先确认、缺失时回到 Propose（`helixharness/skills/tdd/SKILL.md:13,25-27`），这一点比任何单项技能都关键。处理：验证入口成为合同必填字段，由引擎核对验收、方案与审查三方（设计 §2）。

**StoryLoop 的优势**（不应被替换）：外部闭环持有判定权，代理声明不是证据；实现前由独立角色盲写、受保护的验收套件，并在候选副本证明 RED；特征化锁定旧行为；底座与判据的边界审计。HelixHarness 的插件主链主要靠技能和提示约束代理纪律，没有等价的机器强制。

**融合方式**：HelixHarness 的价值多在技能（TDD、决策树、影响分析、系统化排错、审查分级），把它们挂到 StoryLoop 已按权限划分的角色上，比引入其工作流更好。归类规则：违反会造成误判通过/越界/数据损坏、能确定性检查、改变状态流转或恢复、属于跨角色契约或评测度量的放引擎；其余方法与判断质量放技能（设计 §1）。

**知识维护**：人指出初版遗漏了知识库与记忆的维护。对照 HelixHarness 的 `docs/knowledge` 与 `docs/harness` 分层和精确 diff 授权（`helixharness/workflows/single/archive.md:132-148`），StoryLoop 补了 PASSED 后的 foundation 精确 diff 增量通道、角色用前核实、事实与规范分节；经验/失败记忆、活规格和 BASE 保护已强于对方，保持不变（设计 §6）。
