# 研究来源与设计取舍

调研日期：2026-09-12。以下是方法与机制参考；本工程独立实现，没有复制它们的源码或宣称兼容其全部工作流。

- [Cucumber Example Mapping](https://cucumber.io/docs/bdd/example-mapping/)：用故事、规则、例子和问题共同澄清产品。StoryLoop 对话产物保留这四类信息。
- [Cucumber Three Amigos](https://cucumber.io/docs/bdd/who-does-what/)：产品、QA 和工程各自补充视角；业务取舍由人决定。
- [BMAD 需求与规格](https://docs.bmad-method.org/plan/define-requirements-and-a-specification/)：从自由表达开始的产品协作。
- [BMAD 自治循环](https://docs.bmad-method.org/build/autonomous-development-loops/) / [bmad-loop](https://github.com/bmad-code-org/bmad-loop)：单故事实施、独立审查、有限重试与持久化状态。
- [TEA ATDD](https://bmad-code-org.github.io/bmad-method-test-architecture-enterprise/how-to/workflows/run-atdd/)：风险驱动验收设计。StoryLoop 只有执行到目标行为断言失败才标 RED。
- [bmad-loop TEA 集成](https://github.com/bmad-code-org/bmad-loop/blob/main/docs/tea-plugin-guide.md)：当前集成存在开发后运行 ATDD 与建议式门禁。StoryLoop 将验收前移，并对缺失或无效证据拒绝通过。
- [GitHub Spec Kit](https://github.github.com/spec-kit/reference/agentic-sdd.html)：先检查需求完整性和一致性，再拆解与实施。
- [OpenSpec 工作流](https://github.com/Fission-AI/OpenSpec/blob/main/docs/workflows.md)：显式组织规格与变更，适合持续演进；StoryLoop 以批准基线和过期证据处理变化。
- [Superpowers TDD](https://github.com/obra/superpowers/blob/main/skills/test-driven-development/SKILL.md)：实际观察红、绿与重构的开发纪律；区分提示词规范与机器强制。
- [Anthropic 长任务 Harness](https://www.anthropic.com/engineering/harness-design-long-running-apps)：生成与独立评估分工、实际操作产品和明确评价标准。
- When 项目的 Loop 工程：参考外部 Judge、失败证据回填、状态恢复和保护路径思路。此工程不依赖 When 业务模块或课程编号。
