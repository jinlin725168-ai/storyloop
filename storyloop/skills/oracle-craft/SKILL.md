---
name: oracle-craft
version: 1.0.0
description: 如何写 StoryLoop 的判据：验收矩阵的行类型、特征化测试、结构断言、执行器退出码。product、acceptance、characterize、review 角色使用。
---

# 判据写法

判据在实现之前写好，实现者改不了；它必须能证明自己真的运行过、真的有约束力。

## 适用角色

- product：给每条验收条件标 `row_type` 与 `oracle_class`，覆盖七个行类型或写明豁免理由；写消费端矩阵；fix 先追调用链再写根因（每条带 `path:line`），把报告者的猜测单列；refactor 写可断言的结构目标。
- acceptance：按 `references/acceptance-matrix.md` 写用例；refactor 按 `references/structural-assertions.md` 写结构断言；为每个测试标 `tier`。
- characterize：按 `references/characterization.md` 锁定现有行为。
- review：检查弱断言（只断言存在、非空）、被删除的测试、用 mock 顶替的集成判据。

## 执行器退出码

- 0：全部通过
- 1：行为断言失败（只有这个算 RED）
- 2：环境错误：收集失败、导入错误、夹具错误、依赖起不来

测试工具的汇总行（例如 `Ran N tests`、`N passed`）要保留在输出里，引擎靠它统计执行数。
