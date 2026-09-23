# 0009 冒烟缺陷走 smoke-repair，从合同提交重证 RED

日期：2026-09-21

## 问题

盲写的冒烟用例可能有缺陷（严格模式定位器、加载遮罩时序、时钟回拨）。实现代理不能改它们，人手改又破坏了"套件受保护"的前提。

## 决定

`storyloop smoke-repair --change ID --reason TEXT`：只在 `IMPLEMENTING`、`BUDGET_EXHAUSTED`、`NEEDS_INPUT` 可用。冒烟角色拿到现有套件和最近失败证据修复自己的套件（不得放宽行为断言），引擎以 `test(<id>): repair smoke suite` 提交，再用 `git archive` 取合同提交加当前套件重建候选副本证明 RED。之前的尝试作废、预算归零，修复记录进运行状态和 decisions.md。

## 理由

- 修复后的套件仍然是"实现之前就会红"的套件，保护前提不变。
- 由人判断"是用例错了"并留下理由，避免代理用改测试的方式过关。

## 代价

- 多一次 RED 证明的构建时间。
