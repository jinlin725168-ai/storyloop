# 接口与配置

对应引擎版本：0.1.0，核对日期 2026-10-11。本页是对外接口的权威清单：命令、状态与退出码、配置、代理协议、目标仓库里的文件约定。原 `docs/protocols.md` 并入本页。

## 一、命令

入口：`storyloop`，等价于 `python -m storyloop`（`python -m storyloop.cli` 也可用）。每个子命令都接受 `PATH`（目标仓库根目录）和 `--json`（输出完整 JSON）。除 `init`、`skills`、`ci` 外，都要求仓库已 `init`。

| 命令 | 参数 | 返回状态 |
|---|---|---|
| `init` | `[--settings FILE]` | `BASELINE_LOCKED` |
| `skills` | — | `SKILLS_INSTALLED` |
| `survey` | `[--reason TEXT] [--from FILE]` | `SURVEYED` |
| `change` | `--idea TEXT` 或 `--change ID --message TEXT`；`[--no-interactive]` | 变更状态（`DRAFT`） |
| `approve` | `--change ID [--no-interactive]` | 运行状态（见第二节） |
| `run` | `--change ID [--no-interactive]` | 运行状态 |
| `status` | `--change ID` | 变更状态，附 `run`、`telemetry`、`next` |
| `log` | `--change ID` | `LOG` |
| `answer` | `--change ID --answer TEXT [--answer TEXT …]` | `ANSWERED` |
| `accept` | `--change ID --criterion ID [--criterion ID …] --note TEXT` | `ACCEPTED`，或仍有未确认条件时 `MANUAL_ACCEPTANCE_REQUIRED` |
| `reset` | `--change ID --reason TEXT` | `IMPLEMENTING` |
| `acceptance-repair`（旧名 `smoke-repair`） | `--change ID --reason TEXT` | `ACCEPTANCE_REPAIRED` |
| `document` | `--change ID [--design FILE] [--capability NAME]` | `DOCUMENTED` |
| `smoke-propose` | `--change ID` | `SMOKE_PROPOSED` |
| `smoke-apply` | `--change ID` | `SMOKE_APPLIED` 或 `SMOKE_UPDATE_FAILED` |
| `base-propose` | `--path P [--path P …] --reason TEXT [--allow-mutable] [--allow-ci]` | `PROPOSED` |
| `base-update` | `--proposal BASE-ID` | `BASELINE_UPGRADED` |
| `ci` | `--change ID` | `PASSED` 或 `FAILED` |
| `report` | `--change ID` | `REPORT`（报告路径） |

各命令的用法见 [使用指南](product-iteration-guide.md)。

## 二、状态与退出码

### 运行状态（`status --json` 的 `run.status`）

| 状态 | 含义 | 能否 `run` 续跑 |
|---|---|---|
| `PREPARING` / `CONTRACT_READY` / `CONTRACT_COMMITTED` | 建分支、物化合同、生成并提交验收套件、证明 RED | 能 |
| `ORACLE_DEFECT` | 应当保持成立的验收条件在旧代码上就失败（用例写错了） | `acceptance-repair` 修正后能 |
| `EXISTING_GREEN` | 验收套件在实现之前就已通过（全部或部分条件），证明不了 RED | `acceptance-repair` 加强套件后能；不修复直接 `run` 只会再停在这里 |
| `IMPLEMENTING` | 方案、特征化、实现—验证—审查循环 | 能 |
| `NEEDS_INPUT` | design、implement 或 review 提了问题 | 回答后能 |
| `BASE_CHANGE_REQUIRED` | 功能触碰底座，已开 BASE 提案 | `base-update` 并合回功能分支后能 |
| `BOUNDARY_VIOLATION` | 代理写了不允许的路径 | 还原后能 |
| `BUDGET_EXHAUSTED` | `max_attempts` 用尽 | 提高预算后能；或 `acceptance-repair` |
| `CHARACTERIZATION_FAILED` | 特征化测试在旧代码上不绿 | 就地修正测试后能（重新验证，不再调用代理） |
| `WORKSPACE_TAINTED` | 实现尝试中出现不该有的改动 | `reset` 后能 |
| `MANUAL_ACCEPTANCE_REQUIRED` | 自动验证与审查全过，等人工条件 | `accept` 全部条件后能；工作区变了要 `reset` |
| `VERIFIED` / `MERGED` / `RECEIPTING` / `FINALIZING` | 完成阶段各步（幂等） | 能 |
| `PASSED` | 已合并、回执已写 | 终态 |

变更状态（`status --json` 顶层 `status`）：`DRAFT`、`APPROVED`、`PASSED`。

### 错误码

命令失败时 stderr 输出一行 JSON：`{"status": <错误码>, "message": …, "next": …}`。

| 错误码 | 含义 |
|---|---|
| `BLOCKED` | 默认：前置条件不满足（未 init、状态不允许、校验失败、`openspec validate` 不通过等） |
| `NEEDS_INPUT` | manual 适配器在等人工回答 |
| `AGENT_UNAVAILABLE` | 代理进程没有给出回答（超时、限流、崩溃、CLI 不可用、进程树没能回收）。实现循环里出现时这一轮交还、不计入 `max_attempts`，记在 `run.agent_failures`；`run` 重试同一轮 |
| `AGENT_CONTRACT_INVALID` | 代理回答修复后仍不符合角色 schema（implement 计一轮失败，不会冒到这里） |
| `WORKSPACE_TAINTED` | 控制面、暂存区或工作区被意外改动（RED、方案、特征化阶段只报错，不落盘为运行状态） |
| `GIT_BLOCKED` / `GIT_CONFLICT` | Git 命令失败 / no-ff 合并失败（功能分支保留） |
| `UNSAFE_CONTAINMENT` | 子进程树没能在受控方式下回收 |

### 退出码

| 退出码 | 情形 |
|---|---|
| `0` | 正常结束，包括停在 `MANUAL_ACCEPTANCE_REQUIRED`（自动验收已全过，只是等人） |
| `2` | 返回状态属于 `FAILED`、`BLOCKED`、`NEEDS_INPUT`、`INTERRUPTED`、`UNSAFE_CONTAINMENT`、`WORKSPACE_TAINTED`、`BUDGET_EXHAUSTED`、`BASE_CHANGE_REQUIRED`、`BOUNDARY_VIOLATION`、`ERROR`、`CHARACTERIZATION_FAILED`、`EXISTING_GREEN`、`ORACLE_DEFECT`、`SMOKE_UPDATE_FAILED`，或任何错误码 |
| `130` | Ctrl-C 中断，状态已保存 |

脚本请判断 `status` 与 `next`，不要只看退出码（例如 `MANUAL_ACCEPTANCE_REQUIRED` 是 0）。

## 三、配置

### settings（`init --settings FILE`）

只接受下表的键，未知键报错。路径类必须是仓库相对路径（不能是绝对路径、盘符、`..`），不能指向 CI 配置，结果去重排序。命令类必须是非空 argv 数组（不经 shell，单条最多 100 项，`{python}` 展开为当前解释器）。

| 键 | 默认值 | 说明 |
|---|---|---|
| `unit_commands` | 自动探测：根目录与一级子目录的 `mvn test`、`gradle test`、`npm/pnpm/yarn test`（有 test 脚本时）、`{python} -m unittest discover` | 单元层；也用于特征化门。旧名 `test_commands`，两者不能同时给 |
| `integration_commands` | `[]` | 集成层 |
| `check_commands` | `[]` | 静态检查层 |
| `test_roots` | `[]` | 测试文件的目录或通配（`*`、`**`）；这里的改动算允许的 `TEST`，也是特征化门的开关之一 |
| `module_roots` | `["modules", "features"]` | 可变区；不能与 CI 文件重叠 |
| `integration_paths` | 自动探测的构建清单（`pom.xml`、`build.gradle*`、`settings.gradle*`、`package.json`、`pnpm-workspace.yaml`、`pyproject.toml`、`setup.py`、`setup.cfg`、`requirements.txt`） | 改动转 BASE 提案 |
| `candidate_allow` | `[]` | 名字敏感但确实是已跟踪配置的文件，允许进入候选副本 |
| `scratch_paths` | `[]` | 代理可以产生的被忽略位置：不含 `/` 的名字在任意层级匹配，含 `/` 的按相对路径前缀匹配 |
| `test_count_patterns` | `[]` | 补充或覆盖执行数统计：`[{"match": <命令里的子串>, "executed": <带命名组 executed 的正则>}]`；内置支持 unittest、pytest、Maven、Gradle、Jest、Vitest、Go |

`module_roots` 与 `test_roots` 不能包含 `.claude/skills`、`.codex/skills` 或它们的上级目录（技能包影响判据的写法，实现者不能拥有）。

本机找不到的可执行文件、Windows 上的 `.cmd/.bat/.ps1` 包装器会被跳过并记为 warning。settings 结果存在 `.storyloop/config.json` 的 `profile` 里：`test_roots`、`scratch_paths`、`candidate_allow` 与各层命令每次现读，可以直接编辑；`module_roots`、`integration_paths` 同时锁在 `baseline.json` 里，只能重新 init（没有进行中变更时）或走 BASE 迭代改变。

### 运行参数（`.storyloop/config.json` 顶层，直接编辑）

| 键 | 默认值 | 说明 |
|---|---|---|
| `adapter` | `"codex"` | `codex`、`command` 或 `manual` |
| `agent_command` | `[]` | `adapter` 为 `command` 时的 argv，支持 `{python}`、`{project}`；不能是 `.cmd/.bat/.ps1` |
| `max_attempts` | `3` | 唯一的质量预算 |
| `agent_timeout_seconds` | `14400` | 代理进程挂死保护 |
| `check_timeout_seconds` | `300` | 单元、集成、静态检查与特征化回归的单条命令上限 |
| `design_min_stories` | `2` | 故事数少于它时跳过 design；必须是 ≥1 的整数 |
| `version`、`mode` | `1`、`"product-iteration"` | 由 init 写入，目前不读取 |

## 四、代理协议（`storyloop.project-agent.v1`）

七个角色都是无状态调用：每次启动新进程，拿到一个请求，必须只返回一个 JSON 对象，退出码 0。请求体上限 2 MB。

```json
{
  "protocol": "storyloop.project-agent.v1",
  "role": "implement",
  "project": "/home/me/work/my-project",
  "payload": {"change": {}, "attempt": 1, "max_attempts": 3, "failures": [], "answers": []},
  "response_schema": {"type": "object", "...": "..."},
  "instructions": "<角色说明，command / manual 适配器附带>"
}
```

- `command` 适配器：请求（含 `instructions`）从 stdin 输入，stdout 返回结果。
- `manual` 适配器：请求另带 `request_id`，写到 `.storyloop/requests/project-<role>.json`；人把 `{request_id, result}` 写到 `.storyloop/responses/project-<role>.json` 后重跑原命令。
- `codex` 适配器：把“角色说明 + `Request:` + 请求 JSON”作为提示词交给 `codex exec`；implement、characterize 用 `workspace-write` 沙箱，其他角色 `read-only`。
- 代理进程只拿到白名单环境变量（`PATH`、`HOME`、`TEMP`、`TMP`、`JAVA_HOME`、`USERPROFILE` 等），名字含 KEY、TOKEN、PASSWORD、SECRET、CREDENTIAL 的一律剔除。
- `openspec/foundation/README.md` 存在时，除 survey 外每个角色的 payload 自动多一项 `foundation: {version, documents:[path]}`。

### 各角色的 payload 与返回

| 角色 | payload | 返回与引擎附加校验 |
|---|---|---|
| `product` | 新建 `{idea}`；修订 `{change_id, message, answers:[{question_id,question,answer}], current_product}` | 完整产品对象（见下）。ID 匹配 `[A-Za-z0-9][A-Za-z0-9_-]{0,79}` 且全局唯一；每个故事至少一条验收条件；`depends_on` 只能引用排在前面的故事；`recommended` 必须在 `options` 里 |
| `acceptance`（生成） | `{change, suite_root}` | `{files:[{path,content}], manifest}`。files 非空、路径相对且不是 CI、不能叫 `manifest.json`、单个 ≤2 MB；测试 ID 唯一；`criteria` 只能引用并必须覆盖全部 `automated` 条件；`timeout_seconds` 1–3600；`tier` 为 `fast`（缺省）或 `heavy`，须与条件的 `oracle_class` 对应；一个测试只能覆盖同一种实现前状态（应红或应绿）的条件 |
| `acceptance`（修复） | `{change: {…, repair:{reason, existing_files, manifest, failure_evidence}}, suite_root}`；`EXISTING_GREEN` 时 `failure_evidence` 为 `{status:"EXISTING_GREEN", criteria_green, results}` | 同上；返回里的 `manifest.json` 文件被忽略 |
| `acceptance`（冒烟维护） | `{mode:"smoke-maintenance", change, acceptance_suite, system_smoke, smoke_root}` | `{files, manifest:{version:1, tests:[{id,description,command,timeout_seconds}]}, rationale}`；测试非空、文件 ≤200 个 |
| `design` | `{change, answers}` | `{summary, touched:[path], design, questions:[string]}`。questions 非空停 `NEEDS_INPUT`；`design` 不能为空；`touched` 必须是合同提交里存在的文件 |
| `characterize` | `{change, test_roots, extend:[path], characterization, design}`（`characterization` 只在之前为 GREEN 时给，`design` 为方案或 null） | `{summary, touched, tests, questions:[]}`。只能写 `test_roots`（否则 `BOUNDARY_VIOLATION`，类别 `NOT_A_TEST_PATH`）；`touched` 必须是既有非测试文件；单元层必须全绿（否则 `CHARACTERIZATION_FAILED`） |
| `implement` | `{change, attempt, max_attempts, failures, answers, characterization, design_plan}`（后两者可为 null） | `{summary, questions:[string], design}`。通过直接改工作区实现；questions 非空停 `NEEDS_INPUT`；结构不符计一轮失败 |
| `review` | `{change, attempt, layers, boundary, design_plan, answers, design}` | `{verdict:"pass"|"fail"|"needs_input", summary, findings:[{message, perspective?, criterion_id?, evidence?, …}], questions}`；`pass` 不能带 findings 或 questions |
| `survey` | `{baseline_version, foundation_root, required_documents, module_roots, integration_paths, reason}` | `{documents:[{path,content}]}`：1–40 份 `.md`、每份 ≤512 KB、八份必需文档齐全、不含配置里的密钥值或带口令的 URL |

survey 的说明由 `foundation-survey` 技能包生成：引擎把 SKILL.md 与 references 原文附在角色说明后（优先用项目 `.claude/skills/` 或 `.codex/skills/` 里的副本）。

### 产品对象

```json
{
  "version": 1, "goal": "…", "non_goals": ["…"], "capability": "supplier-management",
  "clarifications": [{"session": "2026-09-21", "question": "…", "answer": "…"}],
  "questions": [{"id": "Q1", "text": "…", "options": ["A", "B"], "recommended": "A", "why": "…"}],
  "stories": [{
    "id": "S1", "title": "…", "priority": "P1", "independent_test": "…",
    "as_a": "…", "want": "…", "so_that": "…",
    "rules": ["WHEN … THE SYSTEM SHALL …"], "out_of_scope": [], "depends_on": [],
    "acceptance": [{"id": "A1", "given": "…", "when": "…", "then": "…", "kind": "automated"}]
  }]
}
```

- `capability` 是活规格的名字（`openspec/specs/<capability>/spec.md`）；修改既有需求时沿用旧的需求标题，引擎据此做取代记录。
- 每轮最多 5 个问题是角色说明里的要求，引擎不强制；`clarifications` 由引擎合并保留。
- `kind: manual` 只用于机器无法判断的条件：分层验证和审查都通过后、完成之前，运行停在 `MANUAL_ACCEPTANCE_REQUIRED`。

### 返回结构约束（`response_schema`）

来源 `storyloop/role_schemas.py`，是两个 CLI 都能原生执行的严格子集：每个对象 `additionalProperties:false`、全部属性列入 `required`，引擎视为可选的字段声明为可为 `null`。引擎先丢弃可为 null 字段的 `null` 值再校验，所以省略可选字段同样合法；review 的 finding 允许额外键。

适配器应交给 CLI 原生约束（Codex `--output-schema`，Claude `--json-schema`）。不符时 Codex 适配器在同一会话带着具体问题修复（JSON 解析失败与结构不符都算，最多两次），仍不符报 `AGENT_CONTRACT_INVALID`。

### `_telemetry`（可选）

任何角色的返回对象里可以附带 `_telemetry`：数值键 `cost_usd`、`turns`、`duration_ms`、`input_tokens`、`output_tokens`、`segments`、`lanes`、`perspectives`，以及 `model`、`sessions`（会话 ID 列表）。引擎剥离后记入账本：变更调用记在 `.storyloop/state/telemetry/<change-id>.json`，变更创建前的 product 调用先记在 `pending.json` 再收编，survey 记在 `foundation.json`。没有它时只记墙钟时间。

### 诊断记录

每次调用写 `.storyloop/state/telemetry/calls/<调用 ID>/`：`request.json`（脱敏）、`response.json` 或解析失败时的 `response.txt`、`stderr.log`、`meta.json`（结果、错误码、`schema_errors`、修复次数、会话 ID）。账本条目的 `call` 与 `session` 字段指向它。它们用于排障，不是证据。

### 合同的性质字段

| 字段 | 规则 |
|---|---|
| `nature` | `feature`（缺省）/ `fix` / `refactor` |
| `defect` | fix 必填：`observed`、`expected`、`reproduction`、`root_cause`（1–5 条 `{statement, location: "path:line"}`）、`confirmed`、`unconfirmed`；可选 `reporter_hypothesis`、`sibling_entries`、`origin_change` |
| `refactor` | refactor 必填：`subtype`（`structure` / `dependency-swap`）、`scope`、`invariants`、`structural_goals`（至少 1 条）；dependency-swap 必有 `migration_map: [{from, to, assertion}]` |
| `consumers` | `[{id, consumer, trigger, path, in_scope, verification}]` |
| `coverage_exemptions` | `[{row_type, reason}]` |
| `test_removals` | `[{path, reason}]` |
| 验收条件新增 | `row_type`（七选一，缺省 `positive`）、`oracle_class`、`consumers`、`reproduces_defect` |

## 五、验收套件文件约定

变更验收套件在 `acceptance/changes/<change-id>/`（改名前的仓库是 `acceptance/smoke/<change-id>/`，已存在时沿用）：

```json
{"version": 1, "tests": [{"id": "core", "criteria": ["A1", "A2"], "command": ["{python}", "acceptance/changes/FEAT-1/test_core.py"], "timeout_seconds": 900}]}
```

项目级系统冒烟在 `acceptance/smoke/manifest.json`，条目为 `{id, description, command, timeout_seconds}`。

两者都在隔离候选副本的根目录执行：

- 副本由 Git 可见且未忽略的文件构成，排除 `acceptance/ui/`；名字敏感的文件（`.env*`、`*.key`、`*.pem`、`credentials.json`、`secrets.yaml`）必须列入 `candidate_allow`，否则阻断。
- 环境只有 `PATH`、`SYSTEMROOT`、`TEMP`、`TMP`，外加 `PYTHONDONTWRITEBYTECODE`、`PYTHONIOENCODING`。
- 写入副本即判失败。
- 退出码 0 通过、1 行为失败、其他为环境错误；RED 阶段只有 1 算有效红灯，且执行数为 0 的失败不算。
- 每个测试结果带 `counts`（`executed`、`failed`、`skipped`、`parser`），由输出统计；统计不出来为 `null`。
- 证据写在 `.storyloop/evidence/<change-id>/acceptance-red.json` 与 `acceptance-result.json`。
