# StoryLoop 通用工程迭代使用指南

## 1. 适用场景

该模式用于一个已经存在、工作树干净的 Git 仓库。底座、业务模块、OpenSpec、冒烟测试和报告都在同一仓库中；StoryLoop 只在逻辑上锁定底座，不要求拆成多个仓库。

StoryLoop 不会推送远端、不部署，也不会创建或覆盖 GitHub Actions、GitLab CI、Jenkins 等流水线文件。

下文命令以 PowerShell 路径为例；Linux / WSL2 把路径换成本地路径即可（例如 `storyloop init ~/work/my-project`），入口是虚拟环境里的 `storyloop` 或 `python -m storyloop`。

## 2. 一次初始化

```powershell
storyloop init C:\work\my-project
```

初始化要求：

- 路径是 Git 仓库根目录。
- 当前位于有名称的本地分支。
- 已跟踪和未跟踪文件都为空，即工作树干净。

命令会在仓库根目录和一级子目录（monorepo 的 `backend/`、`frontend/` 等）探测 Maven、Gradle、Node、Python 等常见工程，子目录的回归命令自动带上 `-f sub/pom.xml`、`--dir sub` 等参数，记录当前提交、已跟踪文件 SHA-256、模块根目录、构建集成点和回归命令。`.storyloop/` 写入 `.git/info/exclude`，所以本地控制状态不会污染业务提交。

默认业务扩展目录是 `modules/` 和 `features/`。初始化时已跟踪文件均属于稳定底座；常见构建清单（如 `pom.xml`、`build.gradle`、`package.json`、`pyproject.toml`）会被识别为集成点，但任何修改都会转入独立底座迭代，避免功能代理整体改写工程入口。

Windows 上 `.cmd`、`.bat`、`.ps1` 包装器不会被自动执行；对应回归命令会显示 warning，并在报告中明确标记为 `SKIPPED`。如需指定安全的原生可执行程序或显式解释器 argv，可准备：

```json
{"unit_commands":[["{python}","-m","unittest","discover","-s","tests/unit"]],"integration_commands":[["{python}","-m","unittest","discover","-s","tests/integration"]],"check_commands":[["ruff","check","."]],"test_roots":["tests"],"module_roots":["modules","features"],"integration_paths":["pyproject.toml"],"candidate_allow":["frontend/.env.production"]}
```

`unit_commands`、`integration_commands`、`check_commands` 分别是单元测试、集成测试和静态检查（lint、格式、安全扫描）的命令，每层各自出结论，报告分开记；单元与集成在代码里也要分开放（不同目录或包，见 [接入手册](onboarding-a-repository.md)）。旧字段 `test_commands` 等同 `unit_commands`。`test_roots` 声明测试文件所在的目录或通配（`tests`、`backend/**/src/test/**`、`frontend/src/**/*.test.ts`）：它们是 implement 和 characterize 角色可以写的地方，也是特征化门判断"只改了测试"的依据。

`scratch_paths` 列出代理可以在工作区里产生的被忽略位置（目录名或相对路径，如 `target`、`node_modules`、`dist`、`__pycache__`），用于就地构建和测试；未列出的被忽略文件一旦出现即判 `WORKSPACE_TAINTED`。只读的 `git status`/`git diff` 造成的 `.git/index` 刷新总是被容忍，任何暂存、提交或切换分支都会被检测。

`candidate_allow` 列出名字看起来敏感（`.env*`、`*.key`、`*.pem`、`credentials.json`、`secrets.yaml`）但确实是已跟踪配置的文件；未列出的这类文件出现在冒烟和回归的隔离候选副本里会直接阻断。

settings 只接受上面这些键（`unit_commands` 或旧名 `test_commands`、`integration_commands`、`check_commands`、`test_roots`、`module_roots`、`integration_paths`、`candidate_allow`、`scratch_paths`），未知键报错。`max_attempts`（默认 3）、`agent_timeout_seconds`（默认 14400）、`check_timeout_seconds`（默认 300）、`design_min_stories`（默认 2）、`adapter`、`agent_command` 是运行参数，init 写进 `.storyloop/config.json` 后直接编辑。完整表见 [api.md 的配置一节](api.md#三配置)。

然后一次初始化：

```powershell
storyloop init C:\work\my-project --settings C:\work\storyloop-settings.json
```

## 2b. 底座摸底：建立受保护的知识库

摸底是一个独立维护的技能包 `foundation-survey`（源在引擎的 `storyloop/skills/`，含 SKILL.md、八份文档的章节模板与质量标准、按技术栈的探查清单）。先把它装进项目：

```powershell
storyloop skills C:\work\my-project
```

它会写到 `.claude/skills/foundation-survey/`（Claude Code 自动发现）和 `.codex/skills/foundation-survey/`（Codex 自动发现）。未初始化的项目先提交这些文件再 `init`；已初始化的项目会自动开 BASE 提案，提交后 `base-update`。引擎升级后再跑一次即可刷新，`storyloop skills` 的输出会列出每个技能的打包版本与已装版本。

然后让代理把底座摸清楚，两种方式等价：

```powershell
storyloop survey C:\work\my-project --reason "首次摸底"
```

或者在 Claude Code / Codex 会话里直接说"摸底底座"，技能会把文档写到 `openspec/foundation/` 并提示你走 BASE 流程。引擎调用时 `survey` 角色的说明就是这份技能（优先项目里已安装的副本，便于本地修订），所以两种方式产物一致。

`survey` 角色只读探索 README、构建文件、入口与配置、请求链路、数据访问、每个模块的职责、接口与权限串、"加一个功能要碰哪些文件"、可复用的服务和工具、已有测试怎么跑，写出固定的八份文档到 `openspec/foundation/`：

| 文件 | 回答的问题 |
|---|---|
| `README.md` | 索引、对应的底座版本、怎么用这些文档 |
| `modules.md` | 模块组成与依赖方向 |
| `architecture.md` | 架构与运行机制（启动、认证、权限、校验、异常、数据访问、缓存、调度） |
| `extension.md` | 新功能怎么加：每层的文件、模板或生成器、菜单与权限、SQL、前端路由，以及可照抄的参考实现 |
| `api.md` | 现有接口清单（路由、权限串，按模块分组） |
| `capabilities.md` | 底座已经能做什么，含上游自带能力与 `openspec/specs/` 活规格 |
| `building-blocks.md` | 可复用积木：服务、工具类、基类、前端 composables、验收客户端与冒烟工具 |
| `testing.md` | 已有测试与验收基础设施：布局、运行方式、标签或 profile、默认被跳过的东西 |

引擎校验产物（全部为 Markdown、路径限定在 `openspec/foundation/`、八份必须齐全、不得包含仓库配置文件里 password/secret/token 类键的实际值或带口令的连接串；描述键名和位置是允许的），写入后自动开一个 BASE 提案；在原分支提交这些文件，`base-update` 后它们就是受保护的底座。之后每个角色的请求都带 `payload.foundation`（底座版本和文档路径），product 据此不重复建设、修改既有需求时沿用旧标题，implement 按 `extension.md` 放文件、复用 `building-blocks.md` 里的东西，review 把重写已有积木记为 finding。

一次摸底要花代理十来分钟，所以原始返回先保存在 `.storyloop/state/foundation/last-survey.json` 再校验；校验失败时按提示手工修正该文件，用 `storyloop survey PATH --from <该文件>` 直接安装，不必重新摸底。

知识库跟着底座版本走：上游同步或任何改变模块、机制、扩展点的 BASE 迭代之后重新 `survey`，用同样的 BASE 流程更新。人可以直接修订这些文件，也走 BASE 迭代。

## 3. 用一句话创建产品变更

```powershell
storyloop change C:\work\my-project --idea "增加订单取消功能，已发货订单不能取消"
```

AI 返回产品目标、非目标、用户故事、业务规则、Given/When/Then 验收条件和待决问题。草案位于：

```text
.storyloop/drafts/<change-id>/
```

该目录是本地控制面，不进入 Git。先审核：

- `proposal.md`
- `stories.json`
- `acceptance.json`
- `traceability.json`
- `checklist.md`（需求质量门的结果）
- `specs/<capability>/spec.md`（增量规格预览）

需要调整时继续共创，change ID 保持不变：

```powershell
storyloop change C:\work\my-project `
  --change FEAT-20260916-001 `
  --message "取消原因必须填写，管理员也不能取消已发货订单"
```

只要还有未决问题，就不能批准。

### 合同的结构与澄清方式

产品合同沿用 spec-kit 的结构：故事带优先级 P1/P2/P3 和"独立验证方式"，规则用 EARS 句式（WHEN 触发 THE SYSTEM SHALL 行为），验收条件是 Given/When/Then，按 Gherkin 纪律一条一个可观察行为。合同规模跟着请求走：小改动只有一两个故事。

未决的产品歧义以选择题形式提出：角色说明要求每轮最多 5 个，每个 2 到 5 个互斥选项、一个推荐项和一句"为什么重要"（这是提示词约束，引擎只校验推荐项必须在选项里；终端每轮问 5 个）。在终端里执行 `change` 时会逐条提问（回车选推荐项，输入序号或自由文本，`q` 结束本轮），回答立即触发一次修订，代理据此改写规则和验收条件，回答本身进入合同的 `clarifications`（按会话日期记录，之后的修订不会丢失），并出现在 proposal.md 的"澄清记录"里。任何回答都会改变合同摘要，因此旧批准自动失效。`--no-interactive` 或无终端时问题保持开放，用 `change --message` 回答。

### 变更性质与判据

product 角色先判定变更性质，一个变更只有一种：

| 性质 | 合同多出的部分 | 判据 |
|---|---|---|
| `feature` | 无（现状） | 验收套件先红后绿 + 特征化 |
| `fix` | `defect`：现象、期望、复现步骤、报告者猜测（只作背景）、根因（1–5 条，每条 `path:line`，引擎核对在基线上存在）、已确认 / 待确认、同类入口 | 标 `reproduces_defect` 的复现测试先红后绿 |
| `refactor` | `refactor`：子类（`structure` / `dependency-swap`）、范围、不变行为、结构目标、迁移映射表（dependency-swap 必填） | 结构断言先红后绿；特征化不能跳过，仓库必须配置 `test_roots` 与 `unit_commands` |

验收条件可以带 `row_type`（行类型）、`oracle_class`（`auto-fast` / `auto-heavy` / `manual`）、`consumers`（引用合同 `consumers` 消费端矩阵里的 ID）。批准前的检查表会报出：缺复现条件、根因位置不存在、某个行类型既无条件也无豁免、范围内的消费端没被覆盖、重构的特征化未配置。没有 `nature` 的旧合同对这些新检查只给 warning。

验收清单里的测试可以标 `tier: heavy`（端到端、浏览器等慢用例），它们在每轮的快判据全绿后才运行，否则记为 `NOT_RUN`；RED 只由快判据判定。合同 `test_removals` 列出本次允许删除的测试，否则单元或集成层的执行数下降按失败处理。

## 4. 批准后自动完成

```powershell
storyloop approve C:\work\my-project --change FEAT-20260916-001
```

批准是主要的人工闸门（合同里有 `manual` 验收条件时还会停一次）。执行后 StoryLoop 自动：

1. 需求质量门：确定性 checklist 检查合同，阻断项让批准失败；锁定产品合同摘要。
2. 建立 `feature/<change-id>` 本地分支。
3. 将批准合同物化到 `openspec/changes/<change-id>/`（含增量规格），装了 `openspec` CLI 时校验结构。
4. acceptance 角色只看合同盲写 `acceptance/changes/<change-id>/` 变更验收套件（接口优先，浏览器用例只覆盖核心链路），与合同一起提交。
5. 在隔离候选副本里证明验收套件因功能缺失而失败（验收级 RED）。
6. 方案：design 角色（架构师）只读地对照知识库的扩展机制和积木写出方案（Files、Data And Interfaces、Decisions、Risks），并列出将修改的既有文件 `touched`；方案里留有真实决策时停在 `NEEDS_INPUT`。方案喂给后面每个角色，完成时归档为 design.md 的前半部分。小改动自动跳过：故事数少于 `.storyloop/config.json` 的 `design_min_stories`（默认 2，即单故事变更不出方案）时记为 SKIPPED，characterize 与 implement 直接从合同工作；把它设成 1 则每个变更都出方案。
7. 特征化门：characterize 角色以方案的 `touched` 为必锁清单，为这些既有文件写只断言"必须不变"行为的测试（扩展的列表用 contains），引擎要求整个单元层（`unit_commands`）在旧代码加新测试上跑绿、只改了 `test_roots` 内的文件，然后提交。项目没配 `test_roots` 或单元命令时跳过。
8. 调用实现代理（TDD 先红后绿）。代理提出真实产品歧义时停在 `NEEDS_INPUT`；回答不符合角色结构计一轮失败。
9. 边界审计：底座、模块、集成点、测试、系统冒烟和 CI 边界；集成点变化转为底座提案，越界停在 `BOUNDARY_VIOLATION`。实现改了未被特征化锁定的既有文件时，引擎把它们还原到合同提交、补做特征化，这一轮计为失败。
10. 依次跑：单元测试 → 集成测试 → 变更验收 → 系统冒烟 → 静态检查，每层各自出结论；任何一层红就带着证据进入下一轮尝试。验证结束后再审计一次，确认验证没有改动实现。
11. 进行独立审查（对照方案、design 记录的 Tests First、知识库的积木与扩展机制）；`fail` 的 findings 回喂下一轮，`needs_input` 停点。
12. 有 `manual` 验收条件时停在 `MANUAL_ACCEPTANCE_REQUIRED`，等人 `accept` 后 `run` 继续。
13. 提交实现；生成 `reports/storyloop/<change-id>/report.json` 和 `report.md`（含追溯、分层验证、成本）；写 design.md、decisions.md，把增量并入活规格并校验；报告与文档一起提交。
14. 切回原分支执行本地 `--no-ff` 合并，将合并提交写回验收报告，提交合并回执并推进基线血缘。
15. 合并之后（独立步骤，由人触发）：`smoke-propose` 让 acceptance 角色提议把本次核心链路并入或更新项目级系统冒烟，人审核草案后 `smoke-apply` 安装、隔离跑绿、提交。

第 13 步起的完成阶段是幂等的：任何一步崩溃后再次 `storyloop run`，引擎会采纳已存在的实现提交，丢弃上次留下的引擎产物（文档、活规格、报告，以及旧版本可能留下的 `acceptance/ui/` 改动）并重新生成，不会再次调用实现代理，也不会重复提交。

StoryLoop 控制器本身不执行 `git push`。

```mermaid
flowchart LR
    A[一句产品意图] --> B[AI 共创故事和验收条件]
    B --> C{用户批准}
    C --> D[锁定 OpenSpec]
    D --> E[变更验收 RED]
    E --> D2[design 方案]
    D2 --> E2[特征化测试跑绿]
    E2 --> F[实现：TDD]
    F --> G[单元 / 集成 / 验收 / 系统冒烟 / 静态检查 / 审查]
    G -->|失败| F
    G -->|通过| H[追溯报告]
    H --> I[本地 no-ff 合并]
    I --> J[报告记录合并回执]
```

## 5. 在现有 CI 中调用

StoryLoop 只提供一个独立入口：

```powershell
storyloop ci C:\work\my-project --change FEAT-20260916-001
```

在任何 CI 平台中添加一个普通命令步骤即可，它跑该变更的验收套件和项目级系统冒烟。StoryLoop 不会修改以下内容：

- `.github/workflows/*`
- `.gitlab-ci.yml`
- `Jenkinsfile`
- `.circleci/config.yml`
- `azure-pipelines.yml`

命令退出码为 `0` 时两层都通过；失败、环境错误、超时、测试篡改或缺少证据时返回 `2`。执行使用由 Git 可见且未被忽略的文件构成的隔离副本，测试对候选代码的任何写入都会被视为污染，不能通过"先改实现再断言"的方式制造假绿。安全扫描、代码风格这类第五层检查由项目通过 `check_commands` 接进每轮尝试，CI 上原有的扫描流水线保持不变。

## 5b. 每个变更留下的文档（OpenSpec 格式）

- `openspec/changes/<id>/proposal.md`：目标、非目标、用户故事（优先级、独立验证）、澄清记录、待决问题。
- `openspec/changes/<id>/specs/<capability>/spec.md`：批准时生成的增量规格，OpenSpec 格式（`## ADDED/MODIFIED Requirements`，每个故事一个 `### Requirement`，规则为 SHALL 语句，每条验收条件一个 `#### Scenario` 带 GIVEN/WHEN/THEN）。它随合同一起提交并受保护。
- `openspec/changes/<id>/design.md`：前半是 design 角色的方案（Context、Goals、Decisions、Files、Data And Interfaces、Risks），后接 `## Implementation record`（实现代理的 Tests First、Decisions 等）；方案被跳过时只有实现记录。随报告一起提交。
- `openspec/changes/<id>/decisions.md`：引擎生成的人为决策归档：产品修订消息、澄清回答、代理提问的回答、人工验收备注、复位记录。
- `openspec/specs/<capability>/spec.md`：活规格。完成时引擎把增量并入（新增追加、同名需求替换并标注来源变更），格式与 `openspec archive` 的产物一致，`openspec list --specs`、`openspec validate` 可直接使用。变更目录不会被移动到 archive，因为它是受保护的合同与追溯锚点。

### 规格的一致性：三道门与两条出路

- **需求质量门**（批准时）：引擎按 spec-kit 的 checklist 思路对合同做确定性检查，结果写入 `openspec/changes/<id>/checklist.md`（`- [x]` 自动通过，`- [ ]` 需要人判断）。阻断项（未解决的 `[NEEDS CLARIFICATION]`、没有验收条件的故事、未回答的问题）会让 `approve` 拒绝；警告项（缺优先级、缺独立验证方式、规则不是 SHALL 语句、结果含"适当/尽快"之类含糊词、只有单一场景、只有人工条件）记录在案，由批准者权衡。
- **OpenSpec 结构门**（运行时）：装了 `openspec` CLI 时，合同物化后校验增量规格，完成时校验并入后的活规格，不通过就停下不提交；没装则跳过并在运行状态里记为 skipped。
- **取代记录**（完成时）：一个变更修改既有需求（同标题）时，增量规格里的 Scenario 名固定为验收条件 ID，新块没有重述的旧 Scenario 由引擎原样带过来并标注来源；被重述的旧 Scenario 在旧变更的 traceability.json 里记 `superseded_by`，它们的冒烟用例不再代表当前行为。

事后发现规格与代码不一致只有两条出路，引擎不会自动改规格去迁就代码：代码错了，开一个功能变更修；规格过时了，开一个文档型底座变更（`storyloop document` 重新生成后走 `base-propose` / `base-update`）。

### 5c. 五层测试各在哪里

| 层 | 引擎里的名字 | 位置 | 谁写 | 什么时候跑 |
|---|---|---|---|---|
| 系统冒烟 | smoke | `acceptance/smoke/`，跨变更累积，受保护 | acceptance 角色提议、人 `smoke-apply` | 每轮尝试、`ci`、`base-update` |
| 变更验收 | acceptance | `acceptance/changes/<id>/` | acceptance 角色盲写，先红 | 每轮尝试、`ci` |
| 方案 | design | 运行状态与 design.md | design 角色（架构师），只读 | RED 之后、特征化之前一次 |
| 特征化测试 | characterize | `test_roots` | characterize 角色，在旧代码上跑绿，方案的 `touched` 必锁 | 方案之后、实现之前一次 |
| 单元测试 | unit | `unit_commands`、`test_roots` | implement 角色 TDD | 每轮尝试、特征化门、`base-update` |
| 集成测试 | integration | `integration_commands`，独立目录或包 | implement 角色 | 每轮尝试、`base-update` |
| 静态检查 | checks | `check_commands`（离线、确定性、快：Spotless / Checkstyle / oxlint / vue-tsc / gitleaks；漏洞扫描与 SAST 放 CI） | 项目配置 | 每轮尝试 |

系统冒烟的更新永远经人：`smoke-propose --change ID` 之后草案在 `.storyloop/drafts/smoke/<id>/`（用例、manifest.json、rationale.md），可以直接改；`smoke-apply --change ID` 安装到 `acceptance/smoke/`、隔离跑绿后提交 `test(smoke): …`，红的草案不留下任何改动。首次建立系统冒烟也走这条路：在第一个 PASSED 的变更上 `smoke-propose`。克隆仓库或 `base-update` 时引擎采纳已提交的系统冒烟作为受保护基线。功能变更改动 `acceptance/smoke/` 是 `SMOKE_PROTECTED` 越界。

Playwright Test Agents 这类"对着运行中的应用探索并生成用例"的工具不再挂在完成阶段（它们生成的用例第一次执行必然通过，不是证据，而且曾把合并拖慢一小时）；需要浏览器用例进入系统冒烟时，在 `smoke-propose` 的草案阶段用项目自己的工具生成，再由人审核应用。

## 6. 查看状态与报告

```powershell
storyloop status C:\work\my-project --change FEAT-20260916-001 --json
storyloop report C:\work\my-project --change FEAT-20260916-001
```

报告的每条记录包含：

```text
需求 → 用户故事 → 验收条件 → 验收测试 → 实际命令 → 退出码 → 证据 SHA-256 → 实现提交 → 合并提交
```

报告还有"分层验证"一节：单元、集成、变更验收、系统冒烟、静态检查各一行，状态与通过数分开记。

任何验收条件没有真实通过证据，报告都不能标记为 `PASSED`。

指纹的口径：验收证据和人工验收记录绑定的都是**实现指纹**，即 Git 可见文件减去引擎拥有的区域（`openspec/changes/`、`openspec/specs/`、`acceptance/changes/`、`acceptance/smoke/`、`acceptance/ui/`、`reports/storyloop/`，它们各有独立的保护摘要）。因此完成阶段生成文档不会让证据失效；改动任何业务代码或单元、集成测试都会。

## 7. 底座变更如何处理

普通功能迭代修改初始化时的底座文件会暂停为 `BASE_CHANGE_REQUIRED`，并在 `.storyloop/base-proposals/` 生成独立 `BASE-*` 提案。它不会自动把底座路径放宽成业务路径。

底座变更应独立审核、跑完整回归并提升底座版本；处理完成后再回到原功能变更。CI 文件始终受保护，即使它在底座文件中也不会被普通功能代理修改。

计划中的底座改动（升级上游、改构建配置、改代理规范等）不需要先让功能变更撞上边界，直接开提案：

```powershell
storyloop base-propose C:\work\my-project --path pom.xml --path docs/guide.md --reason "升级依赖"
```

上游同步（如 `git subtree pull --squash`）会同时改动可变区里的上游文件，提案加 `--allow-mutable`，这些路径记为 `UPSTREAM_MUTABLE`；路径列表用同步前后的 `git diff --name-only` 得到。

CI 配置（`.github/workflows/*`、`Jenkinsfile` 等）对代理永远是禁区，人要新增或修改时提案加 `--allow-ci`，这些路径记为 `CI_CHANGE`，`base-update` 只接受提案里列出的 CI 路径。

在原分支独立实现并提交提案中的底座路径，确认工作树干净后执行：

```powershell
storyloop base-update C:\work\my-project --proposal BASE-20260916-001
```

该命令要求实际提交路径与提案完全一致、原底座提交仍是祖先；单元、集成、系统冒烟三层都为 PASSED 或 SKIPPED 且至少一层 PASSED；然后把底座补丁版本加一。StoryLoop 不会自动丢弃或搬移功能分支上的未提交内容；这一步由用户按团队 Git 流程处理。

如果提案是由一个正在运行的功能变更撞出来的（`BASE_CHANGE_REQUIRED`），锁定底座后回到功能分支，把源分支合并进来再续跑：

```powershell
git checkout feature/FEAT-20260916-001
git merge --no-edit main
storyloop run C:\work\my-project --change FEAT-20260916-001
```

续跑时引擎发现底座头已前移且功能分支包含它，就把运行的源头改到新底座，记入 `run.base_upgrades`；源分支因其他原因移动仍会在完成阶段被拒绝合并。功能分支上未提交的实现改动由用户处理（丢弃即可，下一轮会重做）。

## 8. 适配器

默认使用本机已安装并登录的 Codex CLI，不保存 API 密钥。Codex 会话持久化在本机（不再 `--ephemeral`），代理以非 JSON 文本结束时引擎用 `codex exec resume` 在同一会话里让它补出 JSON，最多两次；token 用量从事件流记入成本账本（Codex 不报告金额）。`.storyloop/config.json` 也支持：

- `command`：`"adapter": "command"` 加 `"agent_command": [argv…]`（可用 `{python}`、`{project}` 占位），以 argv 启动可信本地代理，stdin 输入请求 JSON，stdout 返回一个 JSON。代理进程只拿到白名单环境变量（`PATH`、`HOME`、`TEMP`、`TMP`、`JAVA_HOME`、`USERPROFILE` 等），名字含 KEY、TOKEN、PASSWORD、SECRET、CREDENTIAL 的一律剔除，所以 `ANTHROPIC_API_KEY` 这类变量传不进去，代理 CLI 要依赖 HOME 下的登录态。`examples/claude-code/` 提供驱动 Claude Code 的现成适配器，`examples/tools/scratch_build.py` 让实现代理在临时目录构建而不污染工作区。
- `manual`：请求写入 `.storyloop/requests/project-<role>.json`，将 `{request_id, result}` 写到 `.storyloop/responses/project-<role>.json` 后重跑原命令；等待期间命令报 `NEEDS_INPUT` 错误码，退出码 2。

实现代理具有仓库写权限，但外部 Loop 会在每次尝试后重新检查 Git 变化和保护摘要；代理自己的“已完成”声明不构成验收证据。

`command` 适配器和项目回归命令是当前用户权限下的原生进程，必须来自可信来源。StoryLoop 可检测本地文件与 Git 状态变化，但不能替代操作系统级网络隔离；如需证明进程无法访问远端，请在无网络且不挂载 SSH/API 凭证的容器或 CI 沙箱中运行。

每次代理调用的墙钟时间都会记录；适配器若在结果里附带 `_telemetry`（费用、轮数、时长、token、模型，`examples/claude-code` 已实现），引擎会剥离并一起记录到 `.storyloop/state/telemetry/<change-id>.json`（变更创建前的 product 调用先记在 `pending.json`、创建后收编；survey 调用记在 `foundation.json`）。验收报告的『成本与耗时』一节汇总每个角色的调用次数、轮数、耗时和费用，以及闭环墙钟和实现尝试轮数；`status --json` 的 `telemetry` 字段给出同样的数据。

每次调用另有一份诊断记录 `.storyloop/state/telemetry/calls/<调用 ID>/`：`request.json`（逐字段脱敏）、`response.json`（或解析失败时的原文 `response.txt`）、`stderr.log`（适配器完整 stderr，脱敏）、`meta.json`（结果、错误码、结构问题、修复次数、代理会话 ID）。账本条目的 `call` 字段指向它，`session` 字段可直接用于 `codex resume <id>` 或 `claude --resume <id>` 回看代理当时做了什么。`storyloop log PATH --change ID` 把调用时间线、每轮失败原因和下一步命令汇总在一屏里。这些是排障材料，不是证据。

预算的含义：`max_attempts` 是唯一的质量预算，连续几轮完整尝试都过不了验收就停下来交给人；`agent_timeout_seconds` 只是进程挂死保护（默认 4 小时），不是用来判断代理能不能做完的；适配器内部的圈数上限是检查点，到上限续同一会话而不是失败。

## 9. 明确停止点

只有以下情况需要人工介入：

- `BASE_CHANGE_REQUIRED`：功能依赖底座修改。
- `NEEDS_INPUT`：已批准合同仍出现真实产品歧义。在终端里执行 `run` 或 `approve` 时会直接逐条提问并继续（回车表示让代理按现有约定自行决定）；非交互环境用 `storyloop answer PATH --change ID --answer "…"`。回答按尝试号记录在 `.storyloop/state/answers/`，随后的实现和审查都能看到问答配对；问题未回答前 `run` 拒绝续跑，`--no-interactive` 可关闭终端提问。
- `WORKSPACE_TAINTED`：代理或其他进程在工作区里产生了不该有的改动（未声明的被忽略文件、暂存、控制面文件）。常见原因：残留的 dev server、未声明的构建目录、代理超时或出错时已改动工作区。只有在实现尝试循环里出现时它才是落盘的运行状态：人先判断原因，处理后执行 `storyloop reset PATH --change ID --reason "…"` 再 `storyloop run`。复位不动任何文件，被中断的那一轮不计入尝试预算，理由写入运行状态。在 RED、方案或特征化阶段出现时它只是一个错误码（`run.status` 仍是之前的状态），清理后直接 `run`，`reset` 会被拒绝。
- `CHARACTERIZATION_FAILED`：特征化测试在旧代码上不绿，说明锁定现有行为的测试写错了。就地修正 `test_roots` 里的这些测试（不用提交）后 `run`：引擎不再调用代理，重跑整个单元层，绿了才提交这些测试并继续；仍然红就再停在这里。修正只能改测试文件，碰到其他路径会停在 `BOUNDARY_VIOLATION`。
- 变更验收用例本身有缺陷（定位器、时序、数据准备）而实现无法通过时：由人判断并执行 `storyloop acceptance-repair PATH --change ID --reason "…"`（旧名 `smoke-repair`）。只在 `IMPLEMENTING`、`BUDGET_EXHAUSTED`、`NEEDS_INPUT`、`EXISTING_GREEN`、`ORACLE_DEFECT` 状态可用。acceptance 角色拿到现有套件文件和最近的失败证据修复自己的套件（不得放宽行为断言；`manifest.json` 由引擎重建，代理不用返回），新套件以 `test(<id>): repair acceptance suite` 提交到功能分支后，引擎从合同提交重建候选副本（`git archive` + 当前套件）重新证明 RED，之前的尝试作废、预算归零，修复记录写入运行状态和 decisions.md。实现代理永远不能改验收用例。
- `MANUAL_ACCEPTANCE_REQUIRED`：自动验收、回归和独立审查都已通过，但合同里有 `manual` 验收条件。此时功能分支上的代码就是待验收版本；逐条执行 `storyloop accept PATH --change ID --criterion A2 --note "看到了什么"`，再 `storyloop run PATH --change ID` 生成报告并合并。验收记录与当时的代码指纹绑定，代码一变就失效。
- `BOUNDARY_VIOLATION`：代理写到了未授权路径。`storyloop log` 找到是哪次调用写的，还原后 `run`。
- `BUDGET_EXHAUSTED`：达到自动尝试上限。用例缺陷走 `acceptance-repair`；否则提高 `max_attempts` 后 `run`，或另开新变更（运行开始后合同不能修订）。
- 存在 `manual` 验收条件：必须提供真实人工证据，不能由 AI 代签。

失败时功能分支和本地状态保留，StoryLoop 不删除分支、不重写历史、不丢弃用户文件。

## 10. 运行状态一览

`storyloop status --json` 的 `run.status`：

| 状态 | 含义 | 下一步 |
|---|---|---|
| `PREPARING` / `CONTRACT_READY` / `CONTRACT_COMMITTED` | 建分支、物化合同、生成并提交验收套件 | 自动 |
| `ORACLE_DEFECT` | 应当保持成立的条件（回归、状态保持、非目标）在旧代码上就失败，用例写错了 | `acceptance-repair` |
| `EXISTING_GREEN` | 验收套件在实现之前就已通过，证明不了 RED | 需求已实现就放弃；用例太弱就 `acceptance-repair` |
| `IMPLEMENTING` | RED 与特征化门已过，实现—分层验证—审查循环中 | 自动；可 `acceptance-repair` |
| `CHARACTERIZATION_FAILED` | 特征化测试在旧代码上不绿 | 就地修正测试后 `run`（重新验证，不再调用代理） |
| `NEEDS_INPUT` | 代理提出真实产品歧义 | 终端提问或 `answer`，再 `run` |
| `MANUAL_ACCEPTANCE_REQUIRED` | 自动验收全部通过，等人工条件 | `accept` 逐条，再 `run` |
| `VERIFIED` / `FINALIZING` / `RECEIPTING` / `MERGED` | 完成阶段各步（幂等） | 自动；崩溃后 `run` |
| `PASSED` | 已合并，报告含合并回执 | 无 |
| `BASE_CHANGE_REQUIRED` | 功能触碰底座 | 在源分支独立提交后 `base-update`，把源分支合进功能分支，再 `run` |
| `BOUNDARY_VIOLATION` | 越界写入 | `log` 找到写入者，还原后 `run` |
| `BUDGET_EXHAUSTED` | 尝试用尽 | 用例缺陷 `acceptance-repair`；否则提高 `max_attempts` 后 `run` |
| `WORKSPACE_TAINTED` | 工作区出现不该有的改动 | 清理后 `reset`，再 `run` |

`acceptance-repair` 命令返回 `ACCEPTANCE_REPAIRED`，运行状态回到 `IMPLEMENTING`、尝试计数归零。变更本身的状态（`status --json` 顶层 `status`）只有 `DRAFT`、`APPROVED`、`PASSED`。各命令的返回状态与错误码见 [api.md](api.md#二状态与退出码)。
