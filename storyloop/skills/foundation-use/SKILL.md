---
name: foundation-use
version: 1.0.0
roles: [product, acceptance, design, characterize, implement, review]
description: 使用 StoryLoop 底座知识前核对当前代码与配置证据，遇到漂移先报勘误。
---

# 底座知识用前核实

`payload.foundation` 给出底座文档路径和版本。按角色只读相关章节，把“已核实事实”当作待核对的定位线索；在依赖某条事实作合同、判据、方案、实现或审查判断前，打开其 `source: path:line` 或配置键位置，对照当前代码和适用范围。只核对定位与非敏感行为，不复制凭证值。

“必须遵守的约束”仍需核对来源、适用条件和是否有更新；观察到的现象不能自行升格为硬规则。README 的全局约束摘要只作导航，细节以对应章节和当前引擎门禁为准。

来源失效、事实与当前代码冲突或配置键改名时，记录原文 claim、文档路径、当前 `path:line` 与 correction，按现有 errata 通道输出勘误草案；停止依赖旧结论，不据此继续做决定。无证据时明确标未知。技能与引擎规则冲突时以引擎为准。

思路参考 HelixHarness 对应文件。
