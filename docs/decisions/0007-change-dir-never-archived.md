# 0007 变更目录不归档，活规格由引擎并入

日期：2026-09-21

## 问题

OpenSpec 的 `archive` 会把变更目录搬到 `archive/`，但 `openspec/changes/<id>/` 是受保护的合同和追溯锚点。

## 决定

完成时引擎把增量规格并入 `openspec/specs/<capability>/spec.md`（新增追加、同名需求替换并标注来源变更），变更目录原地保留，同时写入 design.md 和 decisions.md。被替换的旧场景在旧变更的 traceability.json 里标 `superseded_by`。

事后发现规格与代码不一致只有两条路：代码错了开功能变更；规格过时了用 `storyloop document` 重生成后走 BASE 迭代。引擎不会自动改规格迁就代码。

## 代价

- `openspec/changes/` 会持续增长；这是可接受的，因为每个目录就是一次迭代的档案。
