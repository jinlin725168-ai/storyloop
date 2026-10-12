# 路线图

更新：2026-10-11。历史计划在 `docs/plans/`。

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
- 预算语义修复（2026-10-12）：代理进程没给出回答（超时、限流、崩溃）时报 `AGENT_UNAVAILABLE`，实现循环里这一轮交还、不计入 `max_attempts`，记入 `run.agent_failures`。
- 文档知识库（2026-10-11）：`docs/` 按模块、架构、扩展、接口、能力、积木、测试重组，并对照代码修正漂移。

## 已知问题

暂无。2026-10-11 文档核对时发现的九个问题已全部修复（见上面的"停点修复"一条）。新发现的代码与文档不一致记在这里，修复后移到"已完成"。

## 下一步（按价值排序）

演进方向以 [演进体系总体设计](superpowers/specs/2026-10-12-演进体系-design.md) 为准，按其中六个子项目的顺序推进：变更性质与判据 → 分类与档位 → 报告 → 角色画像 → 分层记忆 → 外环。下列条目中与之重叠的（成本治理、`verify-specs`）并入相应子项目。

1. **接入模板化**：`storyloop init` 按技术栈生成冒烟执行器骨架、AGENT_GUIDE 模板和 settings 草案；把 `smoke_harness.py` 的积木从 vendored 文件变成引擎能力。目标：接入一个新底座从两天缩到半天。
2. **老仓库迁移工具**：把 `acceptance/smoke/<id>/` 搬到 `acceptance/changes/`、settings 自动拆层。
3. **`verify-specs`**：事后检查活规格与代码、冒烟用例的漂移，输出"代码错了"或"规格过时"的分诊建议，不自动改规格。
4. **成本治理**：按变更和角色的预算上限、超限提前停点、`status` 里的实时费用。
5. **上游同步自动化**：一条命令完成 subtree pull、提案、冲突提示、`base-update`。
6. **多变更并行**：依赖声明、分支隔离、合并顺序；目前一次只跑一个变更。
7. **Windows 真实产品迭代的端到端验证**：单元测试已在 Windows CI 全量通过，真实模型闭环尚未在 Windows 上跑过。
8. **品牌与页面替换的样板变更**：验证"改已有目录"而非"新增模块"的变更路径。

## 明确不做

- 不做 CI 系统、部署、push。
- 不做操作系统级沙箱；需要强隔离时用容器。
- 不让引擎自动修改规格迁就代码。
