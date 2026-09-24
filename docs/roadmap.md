# 路线图

更新：2026-09-23。历史计划在 `docs/plans/`。

## 已完成

- 通用工程模式：init 锁底座、change/approve/run、隔离候选副本冒烟、RED 证明、边界审计、no-ff 合并与回执、`storyloop ci`。
- 底座演进：`base-propose`（含 `--allow-mutable` 上游同步）、`base-update`。
- 停点与恢复：`NEEDS_INPUT` 终端提问与 `answer`、`MANUAL_ACCEPTANCE_REQUIRED` 与 `accept`、`WORKSPACE_TAINTED` 与 `reset`、`acceptance-repair`（原 smoke-repair）、幂等完成阶段。
- 需求与规格：spec-kit 结构的合同、选择题澄清、checklist 质量门、OpenSpec 增量与活规格、design.md/decisions.md、`openspec validate` 门、取代记录、`document` 补生成。
- 代理：Claude Code 与 Codex 适配器的会话恢复、预算语义、遥测账本与报告成本节。
- 测试分层（决策 0015）：系统冒烟 + 变更验收 + 特征化门 + 单元/集成分开 + 静态检查；`ui_regression` 撤掉。
- 底座知识库：`foundation-survey` 技能包（`storyloop skills` 装进项目，Claude Code 与 Codex 通用）摸底产出 `openspec/foundation/`，BASE 保护，每个角色先读（决策 0013、0014）。
- 验证：RuoYi 底座三个功能变更、七次 BASE 迭代、上游同步。

## 下一步（按价值排序）

1. **接入模板化**：`storyloop init` 按技术栈生成冒烟执行器骨架、AGENT_GUIDE 模板和 settings 草案；把 `smoke_harness.py` 的积木从 vendored 文件变成引擎能力。目标：接入一个新底座从两天缩到半天。
2. **老仓库迁移工具**：把 `acceptance/smoke/<id>/` 搬到 `acceptance/changes/`、settings 自动拆层。
3. **`verify-specs`**：事后检查活规格与代码、冒烟用例的漂移，输出"代码错了"或"规格过时"的分诊建议，不自动改规格。
3. **成本治理**：按变更和角色的预算上限、超限提前停点、`status` 里的实时费用。
4. **上游同步自动化**：一条命令完成 subtree pull、提案、冲突提示、`base-update`。
5. **多变更并行**：依赖声明、分支隔离、合并顺序；目前一次只跑一个变更。
6. **Windows 回归验证**：已在 GitHub Actions 的 windows-latest 作业上全量通过（2026-09-24，与 Linux 同为必过作业）。修掉的三类问题：套件指纹按 LF 规范化（autocrlf 的检出与合并不再算修改）、候选副本控制文件按解析后的路径定位（8.3 短名）、交互提示在 cp1252 控制台安全输出；假 `openspec` 脚本的测试在 Windows 跳过。真实产品迭代在 Windows 上的端到端验证仍待做。
7. **品牌与页面替换的样板变更**：验证"改已有目录"而非"新增模块"的变更路径。

## 明确不做

- 不做 CI 系统、部署、push。
- 不做操作系统级沙箱；需要强隔离时用容器。
- 不让引擎自动修改规格迁就代码。
