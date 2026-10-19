---
name: oracle-craft
version: 1.1.1
roles: [product, acceptance, characterize, review]
description: 如何写 StoryLoop 的判据：验收矩阵的行类型、特征化测试、结构断言、执行器退出码。product、acceptance、characterize、review 角色使用。
---

# 判据写法

判据在实现之前写好，实现者改不了；它必须能证明自己真的运行过、真的有约束力。

## 适用角色

- product：给每条验收条件标 `row_type` 与 `oracle_class`，覆盖七个行类型或写明豁免理由；写消费端矩阵；fix 先追调用链再写根因（每条带 `path:line`），把报告者的猜测单列；`reproduces_defect: true` 只标在"在有缺陷的现有代码上必须失败"的条件上（行类型只能是 positive、degradation、unknown_state、form_parity），回归、状态保持、非目标这三类在修复前本来就成立，不能标；refactor 写可断言的结构目标。
- acceptance：按 `references/acceptance-matrix.md` 写用例；refactor 按 `references/structural-assertions.md` 写结构断言；为每个测试标 `tier`。
- characterize：按 `references/characterization.md` 锁定现有行为。
- review：检查弱断言（只断言存在、非空）、被删除的测试、用 mock 顶替的集成判据。

## 执行器退出码

- 0：全部通过
- 1：行为断言失败（只有这个算 RED）
- 2：环境错误：收集失败、导入错误、夹具错误、依赖起不来

测试工具的汇总行（例如 `Ran N tests`、`N passed`）要保留在输出里，引擎靠它统计执行数。

## 运行环境与判定时点

- 候选副本里没有 .git：验收用例不能调用 git（git diff、git log 等），要比对的内容在用例里自带（例如记录文件摘要）。
- 前提不满足时必须失败：读不到文件、命令不可用、环境缺依赖时，退出码 2（环境错误）或 1（行为失败），绝不降级成"只检查一部分然后通过"。降级通过的判据是空心判据，审查会判它不合格。
- 判据在合同提交时的代码上判定：RED 证明（包括 acceptance-repair 之后的重证）用的是合同提交时的代码加当前套件。"必须保持"的条件（regression、state_preservation、non_goal）只能引用那时已经存在的文件，不能引用本次变更才新建的文件（例如本次的特征化测试）。
- 过程约束不写成验收条件：例如"既有测试没有被删除或改写"由引擎的执行数下限与独立审查负责，不写成验收条件，也不写测试去检查。

## 判据与合同的归属（review 使用）

审查发现要标出该谁改：

- `implementation`：代码没做到合同要求。默认值。
- `oracle`：验收用例或特征化测试本身写错了。例如断言与合同不符、依赖候选副本里没有的东西（`.git`、网络、本机路径）、引用了本次变更才新建的文件、前提不满足时降级通过。`location` 指向那个测试文件的行。
- `contract`：按合同做不出来。例如两条验收条件互相矛盾，或者某条条件与必须保持的既有行为冲突。`location` 指向与合同矛盾的代码事实，`criterion_id` 写出冲突的条件。

`oracle` 和 `contract` 必须给出合同里存在的 `criterion_id` 和存在的文件 `location`，否则引擎按实现问题处理。不要因为实现难做就标 `contract`；不要因为测试严格就标 `oracle`。
