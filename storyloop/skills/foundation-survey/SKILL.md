---
name: foundation-survey
version: 1.0.0
description: 底座摸底：只读探索一个已有仓库（README、构建文件、代码、接口、已有测试、验收工具），写出模块组成、架构与运行机制、扩展新功能机制、接口清单、能力清单、可复用积木、测试基础设施八份文档，挂到 openspec/foundation/ 作为受保护的底座知识库。当用户说"摸底底座"、"建立底座知识库"、"survey the foundation"、"这个项目怎么扩展新功能"，或 StoryLoop 引擎以 survey 角色调用时使用。只写知识库文档，不改代码。
---

# Foundation Survey：底座摸底

把"底座已经有什么、怎么运行、新功能怎么加"从每个代理各自翻代码，变成一份受保护、随底座版本走的事实。产物是固定的八份 Markdown（文件即接口），后续 product / implement / review 角色起手先读它们。

**设计原则**（吸收自 project-genesis 的文档系列做法）：
- 每份文档有固定章节骨架和质量标准，见 [references/doc-templates.md](references/doc-templates.md)，**起草某份文档前先读它对应的一节**。
- 先证据后结论：每个断言都要能指到路径、类名、函数名或路由；没看过的不写。
- 只描述配置，不抄配置值：口令、密钥、token、连接串一律写"在 `<文件>` 的 `<键>`"。
- 只读：本技能不创建、修改任何源码或配置；产物只落在 `openspec/foundation/`。

## 两种运行方式

| 方式 | 触发 | 产物怎么交付 |
|---|---|---|
| StoryLoop 引擎调用 | 请求 `role: "survey"`，payload 含 `baseline_version`、`foundation_root`、`required_documents`、`module_roots`、`integration_paths` | 最终只输出一个 JSON：`{"documents":[{"path","content"}]}`，`path` 相对 `foundation_root`；引擎负责写入、校验、开 BASE 提案 |
| 交互式（Claude Code / Codex 会话） | 用户要求摸底 | 直接把八份文档写到 `openspec/foundation/`，然后提示用户：`storyloop base-propose <project> --path openspec/foundation/<每个文件> --reason "底座摸底"` → 提交 → `storyloop base-update`；没有 StoryLoop 时直接提交 |

两种方式下探索流程相同。引擎调用时不要写文件，也不要提问；不确定的地方在文档里标 `[待确认]`。

## 摸底流程

```
Phase 0 定界   ── 仓库是什么、可变区在哪、底座版本
Phase 1 骨架   ── 构建文件 → 模块树 → 依赖方向            → modules.md
Phase 2 机制   ── 入口、配置、启动、请求链路、数据、缓存、调度 → architecture.md
Phase 3 表面   ── 控制器/路由/权限串、菜单、字典            → api.md, capabilities.md
Phase 4 扩展   ── 加一个功能要碰哪些文件、模板、参考实现     → extension.md, building-blocks.md
Phase 5 验证   ── 已有测试、标签、默认跳过项、验收工具       → testing.md
Phase 6 索引   ── README.md + 自检门禁
```

### Phase 0 定界

1. 读根 `README*`、`docs/`、`AGENTS.md`、`CLAUDE.md`、`acceptance/AGENT_GUIDE.md`（存在的话）。
2. 记下可变区（`module_roots`）和集成点（`integration_paths`）：知识库描述的是**可变区之外的底座**，以及可变区里已有的业务能力。
3. 记下底座版本（`.storyloop/baseline.json` 的 `baseline_version`，或引擎 payload 给的值）。
4. 识别技术栈，按 [references/survey-checklist.md](references/survey-checklist.md) 里对应的清单探索。

### Phase 1 骨架 → modules.md

- 从构建文件的模块列表出发（`pom.xml` 的 `<modules>`、`pnpm-workspace.yaml`、`pyproject`），每个模块一行：路径、职责、对外提供什么、依赖谁。
- 依赖方向用一张 mermaid 图表达；标出"跨模块只能经由哪一层"（例如只能通过 api 模块的接口）。
- 门禁：每个模块都有职责一句话；没有模块只写"工具"或"其他"。

### Phase 2 机制 → architecture.md

按顺序找：入口类/文件 → 配置文件与 profile → 启动时做了什么（自动装配、初始化数据、缓存预热）→ 一个 HTTP 请求经过什么（过滤器、认证、权限注解、参数校验、统一返回、异常处理）→ 数据访问（ORM、分页、多租户、逻辑删除、审计字段自动填充）→ 缓存、消息、定时任务、文件存储 → 前端的请求封装、路由生成、权限指令、字典机制。每一条给出实现所在的路径或类名。

门禁：能回答"新接口从进入到返回经过哪些环节"和"一个查询怎么被分页、怎么被审计"。

### Phase 3 表面 → api.md、capabilities.md

- `api.md`：按模块列出路由前缀、主要接口、权限串、返回结构；上游自带的系统能力也要列。
- `capabilities.md`：底座已经能做什么，分"上游自带"和"本仓库已实现"。后者以 `openspec/specs/*/spec.md` 为准，逐个能力一段：名字、需求标题列表、入口路径。不要重述规格细节，指向文件即可。

门禁：`capabilities.md` 里每个能力都能对应到 `api.md` 的接口或页面。

### Phase 4 扩展 → extension.md、building-blocks.md

- `extension.md`：以"新增一个业务表的完整功能"为例，按层列出要创建或修改的每个文件（实体、BO/VO、Mapper、Service、Controller、SQL、菜单、前端 api/types/views、路由如何出现），可照抄的参考实现路径，代码生成器模板路径，命名与包约定，权限串与菜单的对应关系，跨模块调用的唯一途径。再列"修改既有功能"的路径。
- `building-blocks.md`：实现时应该复用而不是重写的东西：基类、工具类、通用服务（字典、用户、部门、文件、导出）、前端 composables 与组件、验收客户端与冒烟工具。每项：名字、路径、一句话用途、典型调用。

门禁：一个没接触过仓库的人照 `extension.md` 能列出新增功能要动的全部文件。

### Phase 5 验证 → testing.md

- 已有单元测试和集成测试在哪、怎么跑、用什么框架；构建默认是否跳过测试；测试标签或 profile 的筛选规则（例如只跑某个 `@Tag`）。
- 验收基础设施：冒烟执行器怎么把应用跑起来（临时库、端口、profile）、验收客户端、UI 回归资产目录、self-test。
- 门禁：给出的运行命令必须是从仓库里的配置推出来的，并注明"默认会被跳过的东西"。

### Phase 6 索引 → README.md 与自检

`README.md` 列出八份文档的一句话用途、对应底座版本、生成日期、怎么用（product 看 capabilities，implement 看 extension 与 building-blocks，smoke 看 testing）、已知空白（`[待确认]` 汇总）。

交付前逐项自检：
- [ ] 八份文档齐全，章节符合模板。
- [ ] 每个断言有路径、类名或路由；没有凭印象写的内容。
- [ ] 没有任何口令、密钥、token、带口令的连接串；配置只写键名和文件位置。
- [ ] 语言与仓库自身文档一致。
- [ ] 引擎调用时：最终输出只有一个 JSON 对象，`path` 相对 `foundation_root`，覆盖 `required_documents` 的每个名字。

## 什么时候重新摸底

底座版本变了才需要：上游同步（subtree pull）、改变模块或机制的 BASE 迭代。普通功能变更完成后 `capabilities.md` 会过时一点，但活规格 `openspec/specs/` 是准的；累积几个变更后再刷新即可。
