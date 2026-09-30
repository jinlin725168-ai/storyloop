# 集成协议

本页描述 StoryLoop 的适配器协议。所有文件为 UTF-8 JSON。未知字段、错误类型或缺失必需字段会被拒绝。

协议名是 `storyloop.project-agent.v1`。早期独立实例模式（`storyloop.agent.v1`、检查清单）与 v2 Stage 协议已随代码删除，见本地 tag `archive/legacy-modes`。

## 代理协议（storyloop.project-agent.v1）

`.storyloop/config.json` 的 `adapter` 为 `codex`（默认）、`command` 或 `manual`。四个角色都是无状态调用：每次启动新进程，stdin 收到一个请求，stdout 必须只输出一个 JSON 对象，退出码 0。

```json
{
  "protocol": "storyloop.project-agent.v1",
  "role": "implement",
  "project": "/home/me/work/my-project",
  "payload": {"change": {}, "attempt": 1, "failures": [], "answers": []},
  "instructions": "<角色说明，command/manual 适配器附带>"
}
```

| 角色 | payload | 必须返回 |
|---|---|---|
| `product` | 新建：`{idea}`；修订：`{change_id, message, answers:[{question_id,question,answer}], current_product}` | 完整产品对象（spec-kit 结构，见下） |
| `acceptance` | `{change, suite_root}`；修复模式 `change.repair` 为 `{reason, existing_files:[{path,content}], manifest, failure_evidence}`；冒烟维护模式 `{mode:'smoke-maintenance', change, acceptance_suite, system_smoke, smoke_root}` | 前两种：`{files:[{path,content}], manifest}`；维护模式：`{files, manifest:{tests:[{id,description,command,timeout_seconds}]}, rationale}` |
| `design` | `{change, answers}`（+ foundation） | `{summary, touched:[path], design:markdown, questions:[string]}`；只读；`touched` 必须是合同提交时存在的非测试文件 |
| `characterize` | `{change, test_roots, extend:[path], characterization, design:{summary,touched,design}}` | `{summary, touched:[path], tests:[path], questions:[]}`；只能写 `test_roots` 内的文件 |
| `implement` | `{change, attempt, failures:[…], answers, characterization:{touched, tests}, design_plan:{summary,touched,design}}` | `{summary, questions:[string], design}`；通过直接改工作区实现，只能改 `touched` 里的既有文件和新文件 |
| `review` | `{change, attempt, layers:{…}, boundary, answers, design, design_plan}` | `{verdict:'pass'|'fail'|'needs_input', summary, findings:[object], questions:[string]}` |
| `survey` | `{baseline_version, foundation_root, required_documents, module_roots, integration_paths, reason}` | `{documents:[{path,content}]}`，路径相对 `openspec/foundation/`，只读探索 |

`survey` 的 `instructions` 由 `foundation-survey` 技能包生成：引擎把 SKILL.md 与 references 原文附在角色说明后（优先项目里 `.claude/skills/` 或 `.codex/skills/` 的副本，否则用引擎打包的副本），三种适配器拿到的说明相同。

`openspec/foundation/README.md` 存在时，除 `survey` 外每个角色的 payload 都多一项 `foundation: {version, documents:[path]}`，角色说明要求先读再动手。

产品对象：

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
- `questions` 每轮最多 5 个选择题；`clarifications` 由引擎写入，代理修订时必须保留。
- `kind: manual` 只用于机器无法判断的条件；实现前它们会让运行停在 `MANUAL_ACCEPTANCE_REQUIRED`。

变更验收套件：`files` 的 `path` 相对 `payload.suite_root`（`acceptance/changes/<change-id>`；改名前的仓库是 `acceptance/smoke/<change-id>`）。`manifest` 为

```json
{"version": 1, "tests": [{"id": "core", "criteria": ["A1", "A2"], "command": ["{python}", "acceptance/changes/FEAT-1/test_core.py"], "timeout_seconds": 900}]}
```

每个 `automated` 验收条件必须出现在某个测试的 `criteria` 里。命令在仓库的隔离候选副本根目录执行，环境只有 PATH 和临时目录变量；副本由 Git 可见且未忽略的文件构成（`candidate_allow` 例外），不含 `acceptance/ui/`，写入副本即判失败。项目级系统冒烟的清单在 `acceptance/smoke/manifest.json`，条目为 `{id, description, command, timeout_seconds}`，同样在候选副本里跑。退出码 0 通过、1 行为失败、其他为环境错误；RED 阶段只有行为失败算有效红灯。

`implement` 角色被要求测试先行：每个行为先写单元测试、看到它因预期原因失败、再写最小实现、绿灯后重构。`design` 是 Markdown（Context、Goals、Tests First 记录每个测试先失败后通过的运行、Decisions 含备选方案、Risks And Trade-Offs、Files），完成时写入 `openspec/changes/<id>/design.md`。引擎不验证 Tests First 的真实性，只验收回归、冒烟和审查结果。`questions` 非空会让运行停在 `NEEDS_INPUT`，人的回答以 `answers` 形式出现在后续调用里。

`review` 的 `pass` 不能同时带 findings 或 questions；`fail` 的 findings 会作为下一次实现尝试的输入。审查角色只读，可运行受限的构建/测试命令。

`implement` 的返回同样可以由适配器内部合成（Claude 适配器：主实现者规划通道、按层并行、主实现者集成），引擎只看到最终的 `{summary, questions, design}`；`review` 的返回可以由适配器内部的多个子会话合成（Claude 适配器默认四个视角 + 主评审），引擎只看到合成后的一个对象；`findings` 里每项建议带 `perspective` 与 `message`。

`response_schema`：请求里附带该角色返回值的 JSON Schema（来源 `storyloop/role_schemas.py`，严格子集：每个对象 `additionalProperties:false`、全部属性列入 `required`，引擎视为可选的字段声明为可为 `null`）。适配器应交给 CLI 原生约束（Codex `--output-schema`，Claude `--json-schema`）。引擎先丢弃可为 null 字段的 `null` 值再校验，所以省略可选字段同样合法；review 的 `findings` 项允许额外键。不符时 Codex 适配器在同一会话带着具体问题修复（最多两次），最终仍不符则报 `AGENT_CONTRACT_INVALID`：implement 计为一轮失败尝试并回喂，其他角色中断运行。

诊断记录：每次调用写 `.storyloop/state/telemetry/calls/<调用 ID>/`（`request.json`、`response.json` 或 `response.txt`、`stderr.log`、`meta.json`），账本条目带 `call` 与 `session`。适配器可在 `_telemetry.sessions` 里回报会话 ID 列表。

`_telemetry`（可选）：任何角色的返回对象里可以附带 `{"cost_usd", "turns", "duration_ms", "input_tokens", "output_tokens", "segments", "model"}`，引擎剥离后记入 `.storyloop/state/telemetry/<change-id>.json`；没有它时只记录墙钟时间。

`manual` 适配器：请求写到 `.storyloop/requests/project-<role>.json`，把 `{request_id, result}` 写到 `.storyloop/responses/project-<role>.json` 后重跑原命令。
