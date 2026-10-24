# EARS 句式

每条规则只写一个义务。规则写外部行为，不写实现步骤；同一业务规则若有两个义务，拆成两条。

## 普遍

句式：`THE SYSTEM SHALL <持续成立的行为>`。

- 正确：“THE SYSTEM SHALL 显示每个待办的标题和完成状态。”
- 常见错误：“系统应该方便使用。”没有可观察义务。

## 事件驱动

句式：`WHEN <触发事件> THE SYSTEM SHALL <响应>`。

- 正确：“WHEN 梅玲把待办‘缴费’设为完成，THE SYSTEM SHALL 不再把它显示为过期。”
- 常见错误：“WHEN 用户操作，系统更新数据。”事件和结果都不具体。

## 状态驱动

句式：`WHILE <状态> THE SYSTEM SHALL <行为>`。

- 正确：“WHILE 待办‘缴费’未完成且到期日早于今天，THE SYSTEM SHALL 将它标为过期。”
- 常见错误：“WHILE 应用运行，系统正常工作。”状态与行为无法验证。

## 不期望行为

句式：`IF <违规或异常条件> THEN THE SYSTEM SHALL <安全响应>`。

- 正确：“IF 到期日为无效日期 THEN THE SYSTEM SHALL 拒绝保存并提示用户更正。”
- 常见错误：“IF 有问题 THEN THE SYSTEM SHALL 报错。”缺少具体条件与可检查结果。

## 可选特性

句式：`WHERE <特性启用条件> THE SYSTEM SHALL <行为>`。

- 正确：“WHERE 项目启用了逾期高亮，THE SYSTEM SHALL 把未完成且到期日早于今天的待办标红。”
- 常见错误：“WHERE 功能可用，系统提供更好体验。”条件和结果含糊。
