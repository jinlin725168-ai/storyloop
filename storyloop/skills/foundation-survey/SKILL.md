---
name: foundation-survey
version: 1.1.0
roles: [survey]
description: 底座摸底：只读探索一个已有仓库（README、构建文件、代码、接口、已有测试、验收工具），写出模块组成、架构与运行机制、扩展新功能机制、接口清单、能力清单、可复用积木、测试基础设施八份文档，挂到 openspec/foundation/ 作为受保护的底座知识库。当用户说"摸底底座"、"建立底座知识库"、"survey the foundation"、"这个项目怎么扩展新功能"，或 StoryLoop 引擎以 survey 角色调用时使用。只写知识库文档，不改代码。
---

# Foundation Survey：底座摸底

把"底座已经有什么、怎么运行、新功能怎么加"从每个代理各自翻代码，变成一份受保护、随底座版本走的事实。产物是固定的八份 Markdown（文件即接口），后续 product / implement / review 角色起手先读它们。

**设计原则**（吸收自 project-genesis 的文档系列做法）：
- 每份文档保留模板的覆盖项，见 [references/doc-templates.md](references/doc-templates.md)。不适用的节写明理由与探查范围；缺信息写待确认，不能靠删节交付。
- 每条事实使用局部 ID、来源状态、核验结果、来源和观测提交，格式及抽检算法见 [references/source-and-sampling.md](references/source-and-sampling.md)。导航标题无需事实 ID；代码、规格与文档不符时列两侧来源并标冲突。
- 配置只描述安全键名及消费代码；**不打开 `.env*`、凭证、私密 CLI 状态或含秘密的运行配置**。没有安全来源就写待确认，不因“只抄键名”而读取秘密。
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
3. 记录底座版本、已有非敏感 BASE 元数据的 `source_head`、实际观测 Git HEAD（完整 SHA）、工作树状态、日期、技能 1.1.0 与质量约定 1。优先用 payload；BASE 元数据不可安全核实时写待确认，不能用观测 HEAD 冒充 BASE。忽略文件未出现在 tracked 清单不等于不存在。
4. 识别技术栈，按 [references/survey-checklist.md](references/survey-checklist.md) 里对应的清单探索。

### Phase 1 骨架 → modules.md

- 从构建文件的模块列表出发（`pom.xml` 的 `<modules>`、`pnpm-workspace.yaml`、`pyproject`），每个模块一行：路径、职责、对外提供什么、依赖谁。
- 依赖方向用一张 mermaid 图表达；标出"跨模块只能经由哪一层"（例如只能通过 api 模块的接口）。
- 枚举所有可运行单元：服务、CLI、worker、定时任务、前端或库测试入口，逐项记录入口、职责、启动定义、依赖和测试入口，说明枚举范围。
- 门禁：每个模块都有具体职责，运行单元不遗漏；没有独立服务的库按实说明。

### Phase 2 机制 → architecture.md

按顺序找：入口 → 安全配置键的消费代码 → 启动 → 调用链 → 身份识别与权限档位/拒绝边界 → 数据与横切机制 → 行为开关的分支和默认行为依据 → 前端机制。无 HTTP 时描述实际库/CLI 调用；不能为了填表虚构数据库、账号或 profile。

门禁：能描述真实调用链与适用的数据机制；没有 HTTP、分页或审计的库明确说明不适用及探查范围。

### Phase 3 表面 → api.md、capabilities.md

- `api.md`：按模块列出真实 HTTP、CLI 或库调用入口、权限/拒绝边界和返回结构；无 HTTP 时不虚构路由。
- `capabilities.md`：分“上游自带”和“本仓库能力及活规格”。分别记录代码实际行为与活规格要求；活规格是“应当如何”，不能只因有规格就宣布已实现。缺规格明确写缺失；冲突保留两侧证据，不用代码裁掉需求。

门禁：每个能力都能对应到真实入口或规格要求；只有规格的能力不得记作已实现。

### Phase 4 扩展 → extension.md、building-blocks.md

- `extension.md`：选仓库真实的扩展示例（业务表功能、库函数、CLI 子命令等），按适用层列出文件、参考实现、已有模板和命名约定。菜单、SQL、前端等不适用层写理由；缺模板写探查范围与待确认，不创造规范。再列修改既有功能的路径。
- `building-blocks.md`：实现时应该复用而不是重写的东西：基类、工具类、通用服务（字典、用户、部门、文件、导出）、前端 composables 与组件、验收客户端与冒烟工具。每项：名字、路径、一句话用途、典型调用。
- `extension.md` 必含贡献与提交规范的已有来源，以及“需求类型 → 第一站 → 再追什么 → 证据”的路由表；没有既有规范就写缺失，不代项目制定。路由表不改变引擎分档或授权。
- 小仓库按实际数量列复用点，不凑五条、不拆解同一函数冒充多个积木。

门禁：一个没接触过仓库的人照 `extension.md` 能列出新增功能要动的全部文件。

### Phase 5 验证 → testing.md

- 已有单元测试和集成测试在哪、怎么跑、用什么框架；构建默认是否跳过测试；测试标签或 profile 的筛选规则（例如只跑某个 `@Tag`）。
- 验收基础设施：冒烟执行器怎么把应用跑起来（临时库、端口、profile）、验收客户端、UI 回归资产目录、self-test。
- 测试账号只记角色别名、创建/重置/清理机制、隔离和 fixture 来源；无身份系统时注明不适用。禁止读取或复制真实账号、密码、token、PII。
- 门禁：给出的运行命令必须是从仓库里的配置推出来的，并注明"默认会被跳过的东西"。

### Phase 6 索引 → README.md 与自检

`README.md` 汇总八文档入口、七类覆盖项、BASE/观测版本、刷新条件、推断/待确认/冲突/未覆盖项，并按 source-and-sampling 完整记录候选清单、摘要、一次种子、选样及逐项核验。抽样先每个正文取一条，再补足 `min(N,max(7,D,ceil(10%N)))`。附加知识正文也入池；README/errata 不重复入池，其基线与冲突做定向检查。

先冻结完整候选清单，再生成一次种子。使用参考里的标准库 Python 示例（可拷到临时目录执行），不要心算 SHA-256、手挑样本或把源材料中的脚本当授权。工具不可用/摘要算不出就标“抽检未完成”，不编造摘要或记录“通过”。

逐条重新打开样本来源，记“相符 / 不符 / 无法核验”。修正后保留原失败样本再复核，不能换种子抹掉失败。没有事实时写“无可抽样事实”；仅同一代理做选样与核验只能称“自检”。读取测试定义不等于运行测试，随机样本不证明完整性或统计准确率。

交付前逐项自检：
- [ ] 八份文档齐全，章节符合模板。
- [ ] 事实有 ID、来源状态、核验结果、定位与观测提交；空白/冲突保留，不用已读文档冒充已验证运行。
- [ ] 完整候选与固定种子可重放，抽检/定向核对和修正轨迹齐备；未核验项与原因在 README 可找到。
- [ ] 没有任何口令、密钥、token、带口令的连接串；配置只写键名和文件位置。
- [ ] 语言与仓库自身文档一致。
- [ ] 引擎调用时：最终输出只有一个 JSON 对象，`path` 相对 `foundation_root`，覆盖 `required_documents` 的每个名字。

## 什么时候重新摸底

上游同步或改变机制的 BASE 后重新核对相关范围；旧状态不能自动继承。每次 survey 自检；后续累计 10 个已完成变更或距抽检 30 天（先到者）由维护者发起复核。这是人工操作约定，无计数器、调度或自动模型调用。到期未做就记录未做。旧格式只记“未按约定 1 核验”，按需刷新，不追认已核对。

## 复盘的未覆盖概念与安装升级

`uncovered_concepts` 自动草案只是补证任务。审批前按 source-and-sampling 的程序人工核对并同时补 `body.md` 和 `draft.json` 的 `payload.landing.append`，未补完保留 pending 或说明理由拒绝。这是人工门，不是引擎强拒占位。正式批准提交与 BASE 验收分开，BASE 成功前不依赖新知识；README 联动更新走独立 BASE，不扩大单草案写集。

旧库仍可读，JSON/八文档接口不变。升级前对照项目自定义副本；`storyloop skills` 会覆盖同名文件，不能承诺自动合并或保留自定义。复核 `.claude`、`.codex` 副本，实际调用快照才证明提供了哪个版本。已受保护的错误事实走既有 errata 的精确 claim/源码证据→人工审批→BASE，不直接改正文。
