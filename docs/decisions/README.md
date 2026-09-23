# 设计决策记录

每条记录回答"当时的问题是什么、决定了什么、为什么、代价是什么"。改变决定时新增一条并标注取代关系，不改旧记录。

| 编号 | 决定 | 日期 |
|---|---|---|
| [0001](0001-external-loop-owns-verdict.md) | 引擎在仓库外面持有判定权，代理的声明不是证据 | 2026-09-12 |
| [0002](0002-smoke-before-implementation.md) | 验收用例在实现前盲写，与合同一起提交，先证明 RED | 2026-09-16 |
| [0003](0003-single-repo-with-subtree.md) | 单仓库底座，多个上游用 git subtree 引入 | 2026-09-17 |
| [0004](0004-foundation-vs-mutable-area.md) | 底座与可变区分离，底座只通过 BASE 迭代演进 | 2026-09-17 |
| [0005](0005-budget-semantics.md) | max_attempts 是唯一质量预算，超时是挂死保护，圈数是检查点 | 2026-09-20 |
| [0006](0006-industry-practices-over-local-plugins.md) | 采用 spec-kit、EARS、Gherkin、OpenSpec、Playwright Test Agents，不自造 | 2026-09-21 |
| [0007](0007-change-dir-never-archived.md) | 变更目录不归档，活规格由引擎并入 | 2026-09-21 |
| [0008](0008-smoke-core-flow-only.md) | 冒烟只含核心链路，UI 回归资产不进冒烟、不是证据 | 2026-09-21 |
| [0009](0009-smoke-repair-channel.md) | 冒烟缺陷走 smoke-repair，从合同提交重证 RED | 2026-09-21 |
| [0010](0010-implementation-digest.md) | 证据与人工验收绑定实现指纹，引擎产物不算 | 2026-09-22 |
| [0011](0011-no-push-no-history-rewrite.md) | 引擎不 push、不删分支、不重写历史 | 2026-09-12 |
| [0012](0012-implementation-tdd-by-prompt.md) | 实现级先红后绿由提示词要求并记录，引擎不证明 | 2026-09-23 |
