# 0006 采用业内实践，不自造需求与测试方法

日期：2026-09-21

## 问题

共创故事和验收用例的方法可以自己发明，也可以用本地已有的插件，但两者都没有外部检验。

## 决定

- 合同结构：GitHub Spec Kit（故事优先级、独立验证方式、澄清最多 5 个选择题、checklist 质量门）。
- 规则句式：EARS（WHEN … THE SYSTEM SHALL …）。
- 验收条件：Gherkin 纪律，一条一个可观察行为。
- 规格演进：OpenSpec 的增量规格与活规格格式，`openspec validate` 可直接用。
- UI 用例生成：Playwright Test Agents（planner/generator/healer）。

顺序：产品角色 → OpenSpec 归档 → checklist 门 → Playwright agents。

## 理由

- 这些方法有社区验证和工具链，新人能查到资料。
- 引擎只负责把它们串成受保护的流程。

## 代价

- 要遵守别人的格式约束（例如 OpenSpec 的 MODIFIED 需求必须带全旧场景）。
