---
name: spec-craft
version: 1.2.0
description: 与人共创需求和规格，产出需求文档和规格文档交给 StoryLoop；适用于“写需求”“需求共创”“帮我把这个想法写成规格”、write a spec、co-create requirements。AI 只引导和起草，值不值得做及例子的预期结果由人拍板。
---

# Spec Craft：需求共创

## 何时使用

人在对话里提出功能想法，需要先评估价值，再写需求与可验证规格时使用。fix、refactor 和人声明的小改动可以跳过评估，须记录理由。只处理共创；批准、编号和质量检查交给 StoryLoop。

## 项目定制

项目里有 `storyloop-custom/spec-craft.toml` 或 `storyloop-custom/spec-craft.user.toml` 时，先运行 `storyloop skills <项目> --view spec-craft`，读取生成的 `.storyloop/skills-view/spec-craft.md` 末尾“项目定制”一节，再按追加的分类、模板节、提问偏好和术语表工作。`question_limit` 可调整每阶段提问上限；定制不能删除或改写内置内容，也不能改变“只能由人判断的事”。

## 五步

| 步骤 | 行动 | 决定者 | 产出 |
|---|---|---|---|
| 0 接入 | 由 StoryLoop 接入流程负责，不在本技能内 | 人确认地图 | 需求地图 |
| 1 评估 | 引导三问，整理回答 | 人决定做、需澄清或放弃 | 评估块 |
| 2 需求 | 按模板起草，猜测标为假设 | 人确认优先级和假设 | 需求文档 |
| 3 规格 | 规则用 EARS，逐条写正例和反例 | 人核对预期结果 | 规格文档 |
| 4 澄清 | 查覆盖地图，逐题提问并写回 | 人回答 | 修订后的文档 |
| 5 交接 | 交给 StoryLoop，见 references/handoff.md | 人批准 | StoryLoop 合同 |

## 工作方式

快速方式由 AI 起草整份，供人集中审；引导方式逐节与人共创。轻档和标准档默认快速，重档默认引导。人随时可以切换。先根据 nature 与人的声明估计档位，StoryLoop 分流后只向上补足。

评估、澄清、规格核对的问句上限分别计数，不跨阶段累加；每阶段默认最多 5 个问题（来源：内置默认）。有项目定制时，以视图末尾显示的生效值和来源为准。评估固定三问，结论仍由人决定。

## 只能由人判断的事

| 事项 | 在哪一步 | 理由 |
|---|---|---|
| 值不值得做 | 1 评估 | 必要性，大模型几乎总是漏判 |
| 优先级与取舍 | 2 需求 | 业务判断 |
| 假设是否成立 | 2 需求、4 澄清 | 假设本来就是 AI 的猜测 |
| 例子的预期结果对不对 | 3 规格 | 正确性，大模型几乎总是漏判；具体数据让产品看得懂 |
| "改到什么程度算新规则" | 3 规格 | 链路骨架 §5.3 |
| 新模块是否成立 | 2 需求 | 链路骨架 D2 |

## 按步骤阅读

- 评估：references/assess.md
- 需求：references/requirement-template.md
- 规格结构：references/spec-template.md
- EARS 句式：references/ears.md
- 正反例：references/examples.md
- 澄清：references/clarify.md
- 交接：references/handoff.md
