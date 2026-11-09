---
name: review
version: 1.0.0
roles: [review, implement]
description: 审查分级、稳定意见 ID 与同一判据的收敛规则。
---

# 审查意见与收敛

一次审查列出当前已发现的全部意见。每条新格式意见写 `id`、`criterion_id`、`target`、`disposition`、`blocking`、`location`、`evidence`、`message`、`required_change`。同一变更中同一判据、归属与位置的根因重审沿用 ID；新根因使用新 ID。建议格式为 `ORACLE-AC-4-01`，不得每轮重新编号。

- `MUST_FIX`：证据已足以确认交付缺陷，`blocking: true`。测试假通过、错误断言、无效 mock/夹具、跳过公共入口或违反测试硬规范都属此类，不能降为建议。
- `DECISION_NEEDED`：须由人决定合同、策略或不可逆取舍，`blocking: true`，给出清楚的问题；引擎停在 `NEEDS_INPUT`。
- `ADVISORY`：有具体证据与可选动作，`blocking: false`；单独出现时仍可通过。人已明确保留、证据和机制未变的稳定 ID 不再追问。

同一 AC 的 oracle `MUST_FIX` 连续两轮阻断按判据强度不足计数。下一轮须在对应 finding 的 `criterion_spec` 一次写全 `inputs`、`preconditions`、`entry`、`expected`、`negative_boundary`、`contract_plan`。新格式合同的 `entry` 以已批准验证入口 ID 开头，再解释调用链；修复理由不得新增合同要求。缺项即停交人。给出完整规格后只做一次定向验收修复，并对最新整体重新审查；不能仅复核原 finding。仍为同类阻断时停止自动循环，保留停点和证据。
