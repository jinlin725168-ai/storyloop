# 路线图

更新：2026-10-24。历史计划在 `docs/plans/`，当前演进计划在 `docs/superpowers/`。

## 已完成

- 通用工程模式：init 锁底座、change/approve/run、隔离候选副本冒烟、RED 证明、边界审计、no-ff 合并与回执、`storyloop ci`。
- 底座演进：`base-propose`（含 `--allow-mutable` 上游同步）、`base-update`。
- 停点与恢复：`NEEDS_INPUT` 终端提问与 `answer`、`MANUAL_ACCEPTANCE_REQUIRED` 与 `accept`、`WORKSPACE_TAINTED` 与 `reset`、`acceptance-repair`（原 smoke-repair）、幂等完成阶段。
- 需求与规格：spec-kit 结构的合同、选择题澄清、checklist 质量门、OpenSpec 增量与活规格、design.md/decisions.md、`openspec validate` 门、取代记录、`document` 补生成。
- 代理：Claude Code 与 Codex 适配器的会话恢复、预算语义、遥测账本与报告成本节。
- 测试分层（决策 0015）：系统冒烟 + 变更验收 + 特征化门 + 单元/集成分开 + 静态检查；`ui_regression` 撤掉。
- 底座知识库：`foundation-survey` 技能包（`storyloop skills` 装进项目，Claude Code 与 Codex 通用）摸底产出 `openspec/foundation/`，BASE 保护，每个角色先读（决策 0013、0014）。
- 验证：RuoYi 底座三个功能变更、七次 BASE 迭代、上游同步。
- 角色按权限边界划分（决策 0016，2026-09-24）：新增 design（架构师）角色，`design_min_stories`（默认 2）让单故事变更跳过方案；Claude 适配器的 review 四视角并行子代理、implement 按层拆通道。
- Windows 回归：GitHub Actions 的 windows-latest 作业全量通过（2026-09-24，与 Linux 同为必过作业）。修掉的三类问题：套件指纹按 LF 规范化、候选副本控制文件按解析后的路径定位（8.3 短名）、交互提示在 cp1252 控制台安全输出。
- 精简（2026-09-30）：角色返回结构由 CLI 原生约束（`--output-schema` / `--json-schema`）；调用诊断记录与 `storyloop log`；每个停点与报错附带可复制的下一步命令；`CHARACTERIZATION_FAILED` 退出码改为 2；删除早期实例模式与 v2 Stage/DAG 引擎（约 4000 行，存档于本地 tag `archive/legacy-modes`）。
- 停点修复（2026-10-11，文档核对时发现）：`CHARACTERIZATION_FAILED` 可就地修正测试后续跑（引擎重新验证，不再调用代理）；`EXISTING_GREEN`（套件在实现前就已通过，全部或部分条件）成为可记录、可续跑的停点，`acceptance-repair` 可从这里加强套件；`acceptance-repair` 返回 `ACCEPTANCE_REPAIRED`、提交为 `repair acceptance suite`；`BUDGET_EXHAUSTED` 的提示不再建议修订合同；`smoke-apply` 失败与 `EXISTING_GREEN` 退出码为 2；`init --settings` 帮助列全八个键；实现指纹范围以代码为准补决策 0017；`project_loop.py` 拆成 loop / gates / guards / finish / acceptance_repair，每个文件都在 500 行以内；删掉测试里已删除模式的遗留夹具。
- 子项目 1 变更性质与判据（2026-10-13）：fix 与 refactor 走通闭环；验收矩阵行类型决定实现前状态（新增 `ORACLE_DEFECT` 停点）；判据自证（执行数统计、基线、零执行与下降判失败）；快重两级判据；`oracle-craft` 技能包；技能包目录保护；报告新增判据自证一节。顺带修复：实现删除已跟踪文件时候选副本构造报错。
- 预算语义修复（2026-10-12）：代理进程没给出回答（超时、限流、崩溃）时报 `AGENT_UNAVAILABLE`，实现循环里这一轮交还、不计入 `max_attempts`，记入 `run.agent_failures`。
- 文档知识库（2026-10-11）：`docs/` 按模块、架构、扩展、接口、能力、积木、测试重组，并对照代码修正漂移。
- 子项目 2a 纠偏：失败归属、作废重开与判据修复已合入；无单独验收文件，[设计 §13](superpowers/specs/2026-10-14-子项目2a-纠偏-design.md#13-实现差异与验收状态)同时记录设计差异与验收状态。
- 子项目 2b 分流：三档、重档审批、拆分建议与 L3 升档已合入；见[设计](superpowers/specs/2026-10-16-子项目2b-分流-design.md#13-实施接口补充与当前边界)和[真实验证](validation/2026-10-17-子项目2b-真实验证.md)。
- 子项目 3 报告：同源 JSON/Markdown 分层报告与调用对账已合入；见[设计](superpowers/specs/2026-10-18-子项目3-报告-design.md)和[验收记录](superpowers/reports/2026-10-18-子项目3-验收.md)。
- 子项目 4 角色画像：角色档次、适配器握手、成本上限与置信度分流已合入；见[设计](superpowers/specs/2026-10-19-子项目4-角色画像-design.md#13-实现差异与验收状态)和[验收记录](superpowers/reports/2026-10-19-子项目4-验收.md)。
- 子项目 5 分层记忆：经验、勘误、审批、取用与复发统计已合入；T2 在 PR #17 补齐自然草案及批准后取用的限定验收，已合入，见下节。详见[设计](superpowers/specs/2026-10-19-子项目5-分层记忆-design.md#14-实现差异与验收状态)和[真实验收](validation/2026-10-20-子项目5-真实验收.md)。
- 子项目 6 外环：指标、规则建议、CI 结果记录和 `metrics` 命令已合入；见[设计](superpowers/specs/2026-10-19-子项目6-外环-design.md#12-实现差异与验收状态)和[验收记录](validation/2026-10-20-子项目6-验收.md)。

- **T3 知识库补充（PR18 已合入）**：技能 1.1.0 实现七类覆盖、逐事实来源、可重放抽检及人工双文本补证；本机专项/core/full 通过。两适配器真实自然产物仍有质量缺口，保留原始结果，见[验收](validation/2026-10-21-知识库补充-验收.md)。
- **T4 接入模板化（PR19 已合入）**：两阶段草案、独占初始化事务及版本化可信 helper，公开 Python/Java+Node 样本与远程 Linux/Windows CI 通过。模板的 Maven 插件路径、真实 RuoYi 接入及半天收益未验证，见[验收](validation/2026-10-21-接入模板化-验收.md)。

- **T5 红魔品牌替换样板（本地闭环完成，待PR合入）**：真实 RuoYi 变更 `FEAT-20261021-002` 完成合同、RED、特征化、GREEN、审查与回执；单元112项、品牌/登录浏览器17项、系统冒烟13项unittest加2项Playwright通过。首轮SUPERSEDED、4次验收修复及代理切换均单列，不声称无人干预首过或T4接入迁移；见[验证报告及metrics](validation/2026-10-21-样板变更-品牌替换.md)。
- **架构收敛 A–D（PR #24、#26、#28、#31）**：架构声明与守护、生命周期归一、声明式变更类型表、底座闭合；分层违规与豁免从 37 条清零，底座内部无环。见[设计](superpowers/specs/2026-10-21-架构收敛-design.md)与 `docs/validation/2026-10-2*-架构收敛-*.md`。
- **设计已批准**：[链路骨架](superpowers/specs/2026-10-23-链路骨架-design.md)（PR #30）、需求共创技能（PR #32）。

## 已知问题

2026-10-11 文档核对时发现的九个问题已修复（见上面的"停点修复"一条）。此前偶发 Git 可见性保护错误仍待捕获具体触发路径，诊断已增强，保护规则未放宽。新发现的代码与文档不一致记在这里，修复后移到"已完成"。

## 下一步

目标是两件事：StoryLoop 能迭代自己（1.0 可用性标准，T6），以及每次迭代都能用指标看出能力变好还是变差。后者分四个子项目：1 链路骨架 → 2 需求共创技能 → 3 能力指标 → 4 测试充分性（调研见共享工作区 `research_notes/规格与测试充分性调研/`）。

```mermaid
flowchart LR
    D["架构收敛 D ✅"] --> T1["骨架 ①"]
    T1 --> E["架构收敛 E"]
    E --> F["架构收敛 F"]
    E --> T2["骨架 ②"]
    S1["spec-craft 第 1、2 批"] --> S3["spec-craft 第 3 批"]
    T2 --> S3
    S3 --> S4["第 4 批"]
    S3 --> S5["第 5 批"]
    T2 --> M["子项目 3 指标"]
    S3 --> A4["子项目 4 测试充分性"]
    E --> T6["T6 自举"]
    T1 --> T6
```

**主线（改引擎，按顺序）**

1. **链路骨架 ①**：带功能短名的变更 ID、每次调用的执行画像、批准时的版本快照。改动小，越早做，带版本的历史数据越早开始积累；也是第一个自举变更的前提。
2. **架构收敛 E**：BASE 迭代并入 base 类型（改变外部行为，单独计划）。T6 的前提。
3. **架构收敛 F**：决策 0018、修订 0004，更新 `architecture.md`、`concepts.md`。
4. **链路骨架 ②**：需求登记，模块、需求、规则、例子的 ID，fix 溯源，docs 类型。与 E 都改合同和生命周期，排在 E 之后。
5. **spec-craft 第 3 → 4 → 5 批**：合同新字段与 `--from-draft`、需求地图起草、product 角色说明迁移。
6. **子项目 3 能力指标**、**子项目 4 测试充分性**。
7. **T6 自举**：设计可在第 4 步期间开始，实现排在 E 与骨架 ① 之后。

**旁线（不碰引擎，随时可做）**：spec-craft 第 1、2 批，以及在 Claude Code 里的真实试用；试用的发现用来校准第 3 批。

按需启动：老仓库迁移工具、上游同步自动化、多变更并行、Windows 真实产品迭代闭环。仅在真实需求或外环信号出现时启动总体设计列出的其他延后项。

## 明确不做

- 不做 CI 系统、部署、push。
- 不做操作系统级沙箱；需要强隔离时用容器。
- 不让引擎自动修改规格迁就代码。
