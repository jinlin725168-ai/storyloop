---
name: review
version: 1.1.0
roles: [review, implement]
description: 审查分级、稳定意见 ID 与同一判据的收敛规则。
---

# 审查意见与收敛

先确定本次审查的批准合同、方案、受保护验收、实际改动和层结果；只对这一轮完整证据下判断。分别检查交付行为与仓库规范，测试文件也在范围内。优先看测试是否真能失败、是否经过已批准入口、是否用 mock 或夹具制造假通过；只有具体维护风险才提非阻断建议。引擎已判定的失败层、停点和范围边界不能由技能改写。

一次审查列出当前已发现的全部意见。每条新格式意见写 `id`、`criterion_id`、`target`、`disposition`、`blocking`、`location`、`evidence`、`message`、`required_change`。同一变更中同一判据、归属与位置的根因重审沿用 ID；新根因使用新 ID。建议格式为 `ORACLE-AC-4-01`，不得每轮重新编号。

`oracle-strength` 预审一次列出套件中全部阻断意见；修复后对最新整套验收重新审查，参照 `previous_findings` 给未解决的根因沿用 ID。意见减少或新增时如实报告，不能只复核上轮清单。

`id` 用于追溯，不是措辞锁：重审后位置行号或 `required_change` 改写时沿用同一 ID 并说明差异；同轮不同意见不得共用 ID。不要因表述漂移把运行判为失败，收敛仍按 AC 与 oracle 阻断归一。

- `MUST_FIX`：证据已足以确认交付缺陷，`blocking: true`。测试假通过、错误断言、无效 mock/夹具、跳过公共入口或违反测试硬规范都属此类，不能降为建议。
- `DECISION_NEEDED`：须由人决定合同、策略或不可逆取舍，`blocking: true`，给出清楚的问题；引擎停在 `NEEDS_INPUT`。
- `ADVISORY`：有具体证据与可选动作，`blocking: false`；单独出现时仍可通过。人已明确保留、证据和机制未变的稳定 ID 不再追问。

同一 AC 的 oracle `MUST_FIX` 连续两轮阻断按判据强度不足计数。下一轮须在对应 finding 的 `criterion_spec` 一次写全 `inputs`、`preconditions`、`entry`、`expected`、`negative_boundary`、`contract_plan`。新格式合同的 `entry` 以已批准验证入口 ID 开头，再解释调用链；修复理由不得新增合同要求。缺项即停交人。给出完整规格后只做一次定向验收修复，并对最新整体重新审查；不能仅复核原 finding。仍为同类阻断时停止自动循环，保留停点和证据。

implement 根据 `MUST_FIX` 的证据做定向修复并重跑覆盖测试；review 对修复后的最新合同、套件、方案、代码和层结果整体重审。人已决定保留的同一 `ADVISORY` ID 不反复追问。所有状态流转、自动修复次数与停点以引擎为准。

思路参考 HelixHarness 对应文件。
