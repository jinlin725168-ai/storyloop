# 集成协议

本页描述 StoryLoop 的适配器协议。所有文件为 UTF-8 JSON。未知字段、错误类型或缺失必需字段会被拒绝。

第一部分是通用工程模式（`storyloop init <已有 Git 仓库>`）使用的 `storyloop.project-agent.v1`；后面的"产品对象/检查清单/Agent 请求"属于早期独立实例模式，v2 Stage 协议在最后。

## 通用工程模式代理协议（storyloop.project-agent.v1）

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

`_telemetry`（可选）：任何角色的返回对象里可以附带 `{"cost_usd", "turns", "duration_ms", "input_tokens", "output_tokens", "segments", "model"}`，引擎剥离后记入 `.storyloop/state/telemetry/<change-id>.json`；没有它时只记录墙钟时间。

`manual` 适配器：请求写到 `.storyloop/requests/project-<role>.json`，把 `{request_id, result}` 写到 `.storyloop/responses/project-<role>.json` 后重跑原命令。

## 产品对象

```json
{
  "version": 1,
  "goal": "员工预约会议室，避免时段冲突",
  "non_goals": ["周期预约"],
  "questions": [],
  "stories": [{
    "id": "ST-1", "title": "预约会议室",
    "as_a": "员工", "want": "预约空闲时段", "so_that": "会议不会冲突",
    "rules": ["已有预约时段不可重叠"],
    "out_of_scope": ["周期预约"], "depends_on": [],
    "acceptance": [{
      "id": "AC-1", "given": "已有 10:00—11:00 的预约",
      "when": "请求 10:30—11:30", "then": "拒绝本次预约", "kind": "automated"
    }]
  }]
}
```

- 故事、验收 ID 稳定且全局唯一；只允许英文字母、数字、横线和下划线。
- 依赖故事必须在清单中排在前面。首版采用确定的顺序执行。
- 未决问题为 `{id,text,options:[string]}`。它们必须通过产品讨论明确解决后才能批准。
- `kind: manual` 用于无法仅靠自动检查决定的业务或体验验收。

## 检查清单

```json
{
  "version": 1,
  "checks": [{
    "id": "CHECK-1", "story_id": "ST-1", "kind": "acceptance",
    "criteria": ["AC-1"],
    "command": ["{python}", "{qa}/booking_check.py", "{workspace}", "overlap"],
    "expected_tests": ["overlap"], "timeout_seconds": 30
  }]
}
```

每个故事还必须有 `kind: health` 的检查，确认执行环境可用。自动验收条目必须由 `kind: acceptance` 的检查覆盖；`regression` 用于额外回归检查。

`command` 是参数数组，没有 Shell 展开。`{python}`、`{workspace}`、`{qa}` 会替换成本机解释器和实例绝对路径。其他语言可以直接调用已经安装的 Java、Node、Go 等命令，或显式解释器与脚本路径。不要把一串 Shell 命令当作一个 argv。

运行目录是 `workspace/`。执行进程只继承常用系统路径/临时目录变量，不继承 API 密钥等应用秘密。测试数据放系统临时目录并自行清理，检查不允许修改持久的实现源文件。

### 检查 stdout

检查必须输出且只输出一个对象，调试日志可以送 stderr，但 Runner 不保留原始 stderr：

```json
{
  "protocol": "storyloop.check.v1",
  "results": [{"id": "overlap", "status": "fail", "message": "预期拒绝重叠预约，实际接受"}]
}
```

| 实际情况 | 进程退出码 | Runner 判断 |
|---|---:|---|
| 所有指定测试均 `pass` | 0 | GREEN |
| 至少一项业务断言 `fail`，其余仅 pass/fail | 1 | RED |
| `error`、`skip`、无结果、额外/重复/遗漏 ID、非法 JSON | 任意 | BLOCKED |
| 返回值与退出码矛盾 | 任意 | BLOCKED |
| 超时、输出超过 1 MiB、进程启动失败 | 任意 | BLOCKED |

`results` 必须与 `expected_tests` 完整对应。错误不能冒充 `fail`。检查脚本的断言是否有效属于可信 QA 的责任，Runner 无法识别脚本伪造的合法 JSON。

## Agent 请求

`config.json` 设 `adapter: command`，并填写 `agent_command`：

```json
{
  "version": 1, "adapter": "command",
  "agent_command": ["{python}", "C:/my-adapters/agent.py"],
  "max_attempts": 3, "max_seconds": 1800,
  "agent_timeout_seconds": 300, "check_timeout_seconds": 60,
  "repeated_failure_limit": 2
}
```

命令适配器的 `{python}`、`{project}`、`{workspace}` 只替换整个 argv 项，避免破坏嵌入 JSON 或代码。不要使用 `.cmd`、`.bat`、`.ps1` 作为裸可执行文件；请显式指定解释器。

每次调用启动新进程，stdin 接收：

```json
{
  "protocol": "storyloop.agent.v1",
  "role": "implement",
  "project": "C:/work/my-product",
  "workspace": "C:/work/my-product/workspace",
  "qa": "C:/work/my-product/qa",
  "payload": {"story": {}, "product": {}, "checks": [], "attempt": 1}
}
```

`payload` 随角色携带上下文。执行与审查时附带 `timeout_seconds`，自定义适配器须遵守；父进程也会强制超时。Agent 的 stdout 必须是单个 JSON 对象，成功退出码为 0。

### 返回值

- **product**：返回完整产品对象。基于已有意图、对话和用户回答保留稳定 ID；不能偷偷移除未回答问题。
- **prepare**：返回 `{checks: <完整检查清单>, qa_files:[{path,content}], workspace_files:[{path,content}]}`。相对路径必须保持在各自目录。任何路径穿越、符号链接、重名、文件/目录冲突或覆盖既有实现都会拒绝整个提案。允许在新基线中替换 QA 文件，但执行前仍须重新批准。
- **implement**：返回 `{summary: string, questions:[string]}`；通过文件操作修改 `workspace/` 中的实现。questions 非空会转入需要业务澄清。
- **review**：返回以下结构：

```json
{
  "verdict": "pass",
  "summary": "独立检查了边界场景，自动验收成立；人工条目仍需产品负责人确认",
  "findings": [],
  "questions": []
}
```

`verdict` 可为 `pass`、`fail`、`needs_input`。fail 必须给出 `{criterion_id,message}` 形式的发现；needs_input 必须提供问题。未知验收 ID、pass 同时包含发现/问题，都会被拒绝。

## 手动桥接已有 AI 会话

1. 设置 `adapter: manual`，其余配置保持完整。
2. 执行 discuss、prepare 或 run。工具显示 `NEEDS_INPUT` 并写 `.storyloop/requests/<role>.json`。
3. 将请求交给你的 AI 会话。实现角色还需把真实文件修改放到 `workspace/`。
4. 将返回结果包裹为 `{request_id: <请求中的 ID>, result: <角色返回对象>}`，写入 `.storyloop/responses/<role>.json`。
5. 重跑原命令。回复只消费一次；时长与重试 bookkeeping 不会使同一个语义请求失配。实现文件若已改好、验收已绿，Runner 可以直接进入新的 QA 请求。

等待人工回复不会消耗实现尝试次数；已执行的环境检查仍消耗实际时间预算。手动模式需要你桥接模型，不是后台自动调用服务。

## 可复用 Python 入口

```python
from pathlib import Path
from storyloop import discovery, engine
from storyloop.storage import initialize

project = Path("C:/work/my-product")
initialize(project, "目标")
# discovery.discuss(project, "补充业务规则")
# discovery.prepare(project)
# engine.approve(project)  # 仅在业务与执行检查已被用户确认后
# engine.run(project)
```

错误类型为 `storyloop.models.StoryLoopError`，包含 `code`。对外程序应读取返回状态，不依赖日志关键词。

## v2 Stage 协议摘要

v2 配置顶层严格为 `version / agent / budgets / stages`。Stage 声明依赖、proposal、写边界、保护边界和按声明顺序执行的 `red/l1/l2/final` Judge argv。未知字段、重叠边界、循环依赖、敏感环境名和 shell 字符串命令均被拒绝。

`loop.agent.v2` 请求包含 `protocol/adapter/role/stage/instruction/payload/request_id`；角色仅为 `implement/review/replan`。响应严格包含 `protocol/request_id/status/summary/questions`，状态为 `ok/needs_input/architecture_decision_required`。Agent 响应永远不含 verdict，summary/questions 不进入权威 GREEN 证据。

`loop.judge.request.v2` 把 request ID 绑定到 Stage、Judge、phase、context、命令摘要和执行前快照摘要。`loop.judge.v2` 的 status/退出码固定为 `pass=0 / fail=1 / error=2 / skip=3`；只有 RED phase 的 fail 是有效 RED，L1/L2/final 只有 pass 是 GREEN。

持久 handoff：

```json
{
  "stage": "foundation",
  "result_digest": "<sha256>",
  "changes": [{"path": "service/business/file.py", "kind": "modified"}],
  "judges": [{"id": "core", "outcome": "GREEN"}]
}
```

replan 输入仅含失败码/指纹、配置中的 Judge ID、累计尝试/耗时和上游 handoff。`loop.replan.receipt.v2` 先原子落盘，再把 `REPEATED_FAILURE` 转为 READY；同一批次崩溃恢复不会重复调用 Agent。

审计目录为 `runs/<stage>/attempt-N/{status,red,green}.json` 与 `runs/final/evidence.json`。所有文件有大小上限并使用严格 JSON；它们只包含时间、累计时长、Adapter ID、状态、摘要、变更和 Judge 白名单证据，不包含 token 假设、payload、stdout/stderr、Agent summary 或文件内容。Stage 状态的 `artifact_chain` 按 attempt 追加规范化文件集 SHA-256，Coordinator 的 `final_artifact_digest` 绑定最终证据；只有尚未绑定的崩溃缺口可以恢复，已绑定历史的缺失、变化或额外目录全部 fail-closed。Coordinator record 还必须能由身份绑定的 Stage 状态重新推导。