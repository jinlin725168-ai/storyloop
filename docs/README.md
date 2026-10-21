# StoryLoop 知识库

对应引擎版本：0.1.0（`storyloop/__init__.py`），最近一次对照代码全面核对：2026-10-11。

这里是 StoryLoop 关于自己的全部事实。结构与引擎给目标仓库生成的底座知识库（`openspec/foundation/`）相同：使用文档回答"怎么用"，工程文档回答"它是什么、怎么改"。

## 怎么用

| 你是 | 先读 | 再读 |
|---|---|---|
| 第一次接触的人 | [concepts.md](concepts.md) | [tutorial.md](tutorial.md) |
| 要把自己的仓库接进来 | [onboarding-a-repository.md](onboarding-a-repository.md) | [smoke-harness.md](smoke-harness.md)、[api.md](api.md) 的配置一节、[faq.md](faq.md) |
| 日常使用者 | [product-iteration-guide.md](product-iteration-guide.md) | [faq.md](faq.md)（按停点组织） |
| 写适配器或接 CI | [api.md](api.md) | [roles.md](roles.md) |
| 改引擎的人或代理 | [modules.md](modules.md) | [extension.md](extension.md)、[building-blocks.md](building-blocks.md)、[testing.md](testing.md)、[contributing.md](contributing.md) |
| 想知道为什么这样设计 | [decisions/](decisions/README.md) | [references.md](references.md) |

## 文档索引

**使用文档**

| 文档 | 用途 |
|---|---|
| [concepts.md](concepts.md) | 解决什么问题、术语表、停点、一次迭代的时序、十条设计理由 |
| [tutorial.md](tutorial.md) | 用 `examples/todo-demo/` 在 30 分钟内跑完一次闭环 |
| [product-iteration-guide.md](product-iteration-guide.md) | 每个命令、每个停点、每个产物的完整说明 |
| [roles.md](roles.md) | 七个角色：权限、输入、输出、是否是门 |
| [smoke-harness.md](smoke-harness.md) | 两阶段草案、可信 case/业务子进程、严格计数与 helper API |
| [onboarding-a-repository.md](onboarding-a-repository.md) | 接入新仓库：settings、冒烟执行器、代理规范、CI、自检清单 |
| [faq.md](faq.md) | 按停点和现象组织的排障 |
| [case-studies.md](case-studies.md) | RuoYi 底座上的真实成本与教训（历史数据） |

**工程文档（知识库八份）**

| 文档 | 用途 |
|---|---|
| [modules.md](modules.md) | 模块清单、分层与依赖方向、引擎在目标仓库里拥有的路径 |
| [architecture.md](architecture.md) | 技术栈、一次变更的链路、状态持久化、运行规则、安全与信任模型、能力边界 |
| [extension.md](extension.md) | 加命令、停点、角色、配置、验证层、技能包分别动哪些文件；不能破坏的不变量 |
| [api.md](api.md) | 命令、状态与退出码、配置键与默认值、代理协议、验收套件文件约定 |
| [capabilities.md](capabilities.md) | 每项能力的入口、实现、决策与守护测试 |
| [building-blocks.md](building-blocks.md) | 已有的积木与"什么时候不该自己写" |
| [testing.md](testing.md) | 引擎自己的测试、夹具、CI、会被跳过的用例 |

**决策与规划**

| 文档 | 用途 |
|---|---|
| [decisions/](decisions/README.md) | 设计决策记录（ADR），带现状列 |
| [roadmap.md](roadmap.md) | 已完成、已知问题、下一步、明确不做 |
| [contributing.md](contributing.md) | 约束、先写测试、改了什么要同步哪份文档 |
| [references.md](references.md) | 研究来源 |
| [superpowers/specs/](superpowers/specs/2026-10-12-演进体系-design.md) | 设计规格。当前是演进体系总体设计：定位、原则、决策分层、六个子项目 |
| `plans/` | 历史实施计划，只读存档，内容不再随代码更新 |

## 已知空白

- 代码与文档不一致之处以代码为准，发现时记在 [roadmap.md 的已知问题](roadmap.md#已知问题)。
- [tutorial.md](tutorial.md) 记录的是 2026-09-29 的一次真实运行，之后新增的 `storyloop log`、`next` 字段已补充说明，但输出样例没有重跑。
- [case-studies.md](case-studies.md) 是历史数据，角色名与完成阶段已加注说明。

## 什么时候刷新

改代码时按 [contributing.md 的文档一节](contributing.md#文档) 在同一个提交里同步。每份工程文档开头的"核对日期"表示最近一次逐条对照代码的时间；超过一个大版本没核对时，按本页的索引重新逐份核对。
