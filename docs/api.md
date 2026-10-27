# 接口与配置

对应引擎版本：0.1.0，核对日期 2026-10-20（子项目 5 工作分支）。本页是对外接口的权威清单：命令、状态与退出码、配置、代理协议、目标仓库里的文件约定。原 `docs/protocols.md` 并入本页。

## 一、命令

入口：`storyloop`，等价于 `python -m storyloop`（`python -m storyloop.cli` 也可用）。每个子命令都接受 `PATH`（目标仓库根目录）和 `--json`（输出完整 JSON）。除 `init`、`skills`、`ci`、`metrics` 外，都要求仓库已 `init`。

| 命令 | 参数 | 返回状态 |
|---|---|---|
| `init` | `[--settings FILE]` | `BASELINE_LOCKED` |
| `init --scaffold` | `[--stack maven\|node\|python\|generic …]`；不得合用 --settings | `SCAFFOLD_CREATED` / `SCAFFOLD_UNCHANGED`（0），`SCAFFOLD_SELECTION_REQUIRED`（2）；均未初始化 |
| `adapters check` | `[--json]` | 能力握手结果 |
| `skills` | — | `SKILLS_INSTALLED` |
| `skills` | `--view spec-craft` | `SKILL_VIEW` |
| `survey` | `[--reason TEXT] [--from FILE]` | `SURVEYED` |
| `change` | `--idea TEXT [--slug NAME]`、`--change ID --message TEXT` 或 `--supersede ID --message TEXT [--reason TEXT]`；`[--no-interactive]` | 变更状态（`DRAFT`） |
| `approve` | `--change ID [--no-interactive]` 或 `--draft KIND/ID`（二者互斥） | 运行状态（见第二节）或 `APPLIED` |
| `run` | `--change ID [--implementer agent\|human] [--overrule TEXT] [--no-interactive]` | 运行状态；新 base 默认代理实现，`ci/upstream` 默认人工，旧 E1 运行保持人工模式 |
| `status` | `--change ID` | 变更状态，附 `run`、`telemetry`、`next`、`stop` |
| `log` | `--change ID` | `LOG` |
| `answer` | `--change ID --answer TEXT [--answer TEXT …]` | `ANSWERED` |
| `accept` | `--change ID --criterion ID [--criterion ID …] --note TEXT` | `ACCEPTED`，或仍有未确认条件时 `MANUAL_ACCEPTANCE_REQUIRED` |
| `reset` | `--change ID --reason TEXT` | 污染前的中断阶段；人工验收复位为 `IMPLEMENTING` |
| `acceptance-repair`（旧名 `smoke-repair`） | `--change ID --reason TEXT` | `ACCEPTANCE_REPAIRED` |
| `document` | `--change ID [--design FILE] [--capability NAME]` | `DOCUMENTED` |
| `smoke-propose` | `--change ID` | `SMOKE_PROPOSED` |
| `smoke-apply` | `--change ID` | `SMOKE_APPLIED` 或 `SMOKE_UPDATE_FAILED` |
| `base-propose` | `--path P [--path P …] --reason TEXT [--allow-mutable] [--allow-ci]` | `PROPOSED` |
| `base-update` | `--proposal BASE-ID [--confirm-oracle-skills]` | 旧 `PROPOSED` 返回 `APPROVAL_REQUIRED`；历史已接受回执返回 `BASELINE_UPGRADED` |
| `base-design-approve` | `--change BASE-ID --plan-id SHA256` | `DESIGN_APPROVED`，具名批准当前代理方案 |
| `base-review` | `--change BASE-ID --verdict pass\|fail --note TEXT [--human-review REASON]` | `REVIEW_RECORDED`；单厂商人工替代须给至少 12 字理由，审查人须独立于全部候选作者和提交者 |
| `ci` | `--change ID` | `PASSED` 或 `FAILED` |
| `metrics` | `[--since YYYY-MM-DD] [--ci-results DIR] [--save]` | `METRICS` |
| `report` | `--change ID` | `REPORT`（报告路径） |
| `drafts` | `[--reject KIND/ID --reason TEXT]` | `DRAFTS` 清单或状态为 `rejected` 的草案；`--reason` 只与 `--reject` 同用 |
| `lessons` | `[--confirm ID \| --retire ID --reason TEXT]` | 经验清单或 `COMMITTED`；确认与退休互斥，退休必须给理由 |
| `lessons-propose` | `--change ID` | `DONE`、`FAILED`、`SKIPPED_NO_SIGNAL` 或 `SKIPPED_COST_LIMIT` 的复盘结果 |

例如 `storyloop change PATH --idea "导出用户" --slug user-export` 会生成形如 `FEAT-20261024-user-export` 的 ID。短名须为 3–40 个小写字母、数字或连字符，首尾为字母或数字。未提供短名时继续使用 `FEAT-20261024-001` 这样的序号格式；已有旧格式 ID 仍可使用。

各命令的用法见 [使用指南](product-iteration-guide.md)。两阶段接入、onboarding.json、`{storyloop_harness}` argv、可信 case 及公开 helper API 见[版本化冒烟执行器](smoke-harness.md)。scaffold 幂等只接受未改草案；人工补齐后按干净提交执行原 init。init 失败按写入内容归属回滚，不覆盖并发修改。

### E2a 底座代理通道

新 base 变更批准后，`run --change BASE-ID` 先完成旧底座绿，再生成方案并停在 `DESIGN_APPROVAL_REQUIRED`。人阅读 `.storyloop/state/base-designs/BASE-ID.json`，用 `base-design-approve --plan-id` 具名批准；下一次 `run` 进入实现，再一次 `run` 调用代理。引擎先在项目工作树外创建 detached Git worktree，代理只以它为工作目录；引擎在该树审计合同批准的精确路径，再把合格内容搬回干净的 base 分支并提交候选。中断时按运行状态保存的路径删除整棵临时 worktree，不自动清理主工作区的人类改动。判据路径（系统冒烟、`acceptance/changes/**`、声明的 `test_roots`、`acceptance/storyloop-settings.json`）在 E2a 仍拒绝代理修改；**E2b 完成前，不得把设计 §4.2 的判据隔离注入跑红视为已交付**。

不同厂商由适配器握手的 `vendor` 判定；缺失或相同都不能证明独立审查，`degrade` 不会把 base 审查回落给实现厂商。只有项目可配置的适配器均证明属于同一个 vendor 时，具名人可用 `base-review --human-review '至少 12 字理由'` 替代代理审查，随后仍须 `accept --criterion BASE-ACCEPT`。报告增量提供 `review_mode`、`human_review_reason`，metrics 单列 `base_human_review_substitutions`；E1 普通人工审查不计替代。

合并源分支之前，代理通道一旦中断或产物失配，会作废方案、候选、证据、审查和验收并回合同提交；源分支是否已包含候选依 Git 事实判断。源分支已合并后仅按既有回执和底座版本流程幂等推进。T6 引擎分离前，本仓库引擎、守卫、适配器及其判据不可交给本仓库代理修改，可显式选 `--implementer human`。E3 的扩展审查能力未在本批实现。

### spec-craft 项目定制视图

`storyloop skills PATH --view spec-craft --json` 只生成 `.storyloop/skills-view/spec-craft.md`，不会安装或修改 `.claude/skills/`、`.codex/skills/`。返回 `status: "SKILL_VIEW"`、`skill`、仓库相对 `path`、视图的 `sha256`、`customized`、按合并顺序排列的 `sources`（每项含 `path`、`sha256`）、`warnings`，以及生效的 `question_limit` 和 `question_limit_source`。视图始终在技能本体末尾的“生效配置”标题下追加生效上限及来源；未生成视图时技能正文的默认上限仍生效。无定制文件时 `customized` 为 `false`，`question_limit_source` 为 `null`（显示为“内置默认”），有定制值时该字段为仓库相对路径。

团队定制放在 `storyloop-custom/spec-craft.toml` 并进入 Git；个人定制放在 `storyloop-custom/spec-craft.user.toml`。`storyloop init` 会把 `storyloop-custom/*.user.toml` 写进 `.git/info/exclude`；未初始化仓库先运行视图命令时，也会在写视图前设置忽略规则，保持工作区干净。已经被 Git 跟踪的个人文件会在 `warnings` 中提示，需由人移出版本库。两层都使用 UTF-8 TOML（可带 BOM），`version = 1`。可用顶层键只有 `version`、每步骤提问上限 `question_limit`（整数 3–10）、字符串数组 `preferences`、仓库内已存在的非符号链接文件相对路径 `glossary`、`[[coverage]]`（`code`、`name`、`check`）和 `[[sections]]`（`code`、`document`、`title`、`guidance`）；`document` 只能是 `requirement` 或 `spec`。未知键、无效路径或格式会报错并指出文件。

合并顺序为团队文件、个人文件：`question_limit` 与 `glossary` 是标量，后者覆盖前者；`preferences` 追加；`coverage`、`sections` 按 `code` 合并，同码替换、异码追加。`question_limit` 默认 5；步骤 1 评估、2 需求、3 规格、4 澄清、5 交接向人提问时各自计数，不跨步骤累加。定制只能追加覆盖地图分类与模板节、调整允许的提问配置，不能删除或改写内置内容、输出格式、引擎约束或“只能由人判断的事”。`spec-craft` 仍只用于对话入口，不下发给任何引擎角色。

### 外环指标 JSON

`storyloop metrics PATH` 默认打印 Markdown；`--json` 返回以下完整结构。`--since` 按变更批准日期（含当日）筛选，必须是 ISO 日期。`--save` 同日覆盖两份 UTC 日期命名的文件，并在返回值增加仓库相对路径 `written`；除此之外命令只读、不调用代理、不要求工作区干净。

```json
{
  "status": "METRICS",
  "generated_at": "2026-10-20T12:00:00+00:00",
  "since": "2026-10-01",
  "changes": 12,
  "metrics": {
    "M1": {
      "id": "M1", "value": 0.75, "numerator": 9, "denominator": 12, "n": 12,
      "changes": ["CHANGE-1"], "missing": [{"change_id": "CHANGE-2", "reason": "UNREADABLE"}]
    }
  },
  "suggestions": [],
  "deferred": [],
  "manual_deferred": [],
  "missing": [],
  "discrepancies": [],
  "written": ["reports/storyloop/metrics/2026-10-20.md", "reports/storyloop/metrics/2026-10-20.json"]
}
```

M1b 是 M1 的尝试次数补充；M12 含 `recurrence_rate` 和 `unclaimed_rate` 两个同形子指标。M11 另给 `calls`、`agent_ms`、`known_cost_calls`、`cost_coverage` 和按 `角色|实际档次` 的 `by`，未知费用不估价。建议项保留规则 ID、触发值、阈值、样本、至多十个证据变更及剩余数量；R3、R7、R11 还分别保留升档规则 ID、CI 失败层、角色和实际档次。样本不足的项用 `insufficient`，证据不足的项用 `unavailable`，不会混成未触发。

指标缺失原因码为 `NO_SAMPLE`、`CI_RESULTS_NOT_PROVIDED`、`LESSONS_NOT_CONFIGURED`、`UNKNOWN_REPORT_VERSION`、`NO_ROUTING`、`NO_TIER_RECORDED`、`UNREADABLE`。本地 PASSED 缺有效 CI 结果时 M6 记 `CI_RESULT_ABSENT`；CI 文件匹配变更但无合并提交时记 `CI_MERGE_COMMIT_ABSENT`。CI 文件提交与变更合并提交无祖先关系时另记 `CI_COMMIT_UNRELATED`。`discrepancies` 的每项为 `{change_id, field, report, run}`，报告快照优先，运行状态只补报告没有的字段。

`storyloop ci PATH --change ID --record FILE` 在正常运行两层 CI 后写出版本 1 的有界 JSON：`change_id`、总 `status`、当前 `commit`、带时区的 `recorded_at`，以及 `layers.acceptance/smoke`。`metrics --ci-results DIR` 只读 `ci-*.json`，同一变更取真实 UTC 时间最新且提交相关的一份；文本子进程显式使用 UTF-8。

## 二、状态与退出码

### 运行状态（`status --json` 的 `run.status`）

| 状态 | 含义 | 能否 `run` 续跑 |
|---|---|---|
| `PREPARING` / `CONTRACT_READY` | 建分支、物化合同、生成验收套件 | 能 |
| `CONTRACT_COMMITTED` | 合同与套件已提交，待证明 RED | 能；套件副作用阻断 RED 时可先 `acceptance-repair` |
| `ORACLE_DEFECT` | RED 检查或审查发现判据缺陷 | `acceptance-repair`；仅审查归属可用 `run --overrule TEXT` |
| `NEEDS_SPEC_FIX` | 审查发现合同缺陷 | `change --supersede ID --message TEXT`，或 `run --overrule TEXT` |
| `NO_PROGRESS` | 根因调查后失败特征未变且无原失败用例转绿 | 处理后 `run`；或修判据、修合同重开 |
| `SUPERSEDED` | 已作废，`superseded_by` 指向新草案 | 不能续跑；批准新变更 |
| `EXISTING_GREEN` | 验收套件在实现之前就已通过（全部或部分条件），证明不了 RED | `acceptance-repair` 加强套件后能；不修复直接 `run` 只会再停在这里 |
| `IMPLEMENTING` | 方案、特征化、实现—验证—审查循环 | 能 |
| `NEEDS_INPUT` | design、implement 或 review 提了问题 | 回答后能 |
| `BASE_CHANGE_REQUIRED` | 功能触碰底座，已开 BASE 提案 | `base-update` 并合回功能分支后能 |
| `BOUNDARY_VIOLATION` | 代理写了不允许的路径 | 还原后能 |
| `COST_LIMIT_REACHED` | 按变更调用/墙钟/token/已知费用达到上限 | 提高 limits 后能，复用已完成候选 |
| `BUDGET_EXHAUSTED` | `max_attempts` 用尽 | 提高预算后能；或 `acceptance-repair` |
| `CHARACTERIZATION_FAILED` | 特征化测试在旧代码上不绿 | 就地修正测试后能（重新验证，不再调用代理） |
| `WORKSPACE_TAINTED` | 工作区出现不该有的改动 | `reset` 后能 |
| `MANUAL_ACCEPTANCE_REQUIRED` | 自动验证与审查全过，等人工条件 | `accept` 全部条件后能；工作区变了要 `reset` |
| `VERIFIED` / `MERGED` / `RECEIPTING` / `FINALIZING` | 完成阶段各步（幂等） | 能 |
| `PASSED` | 已合并、回执已写 | 终态 |

变更状态（`status --json` 顶层 `status`）：`DRAFT`、`APPROVED`、`PASSED`、`SUPERSEDED`。

### 纠偏状态字段与命令约束

新增字段均按需写入，旧状态文件缺省兼容，不需要迁移。

| 字段 | 含义 |
|---|---|
| `run.stop`（`status` 也返回顶层 `stop`） | `{status, phase, completed, last_verdict, attempts, blocker, evidence, facts, questions, options, resume}`；`phase` 为 `contract/prepare/implement/verify/finish`；options 为 `{action, command}` 列表 |
| `run.facts` | 最多 50 条 `{statement, location, role, attempt}`，校验位置后按陈述与文件去重；不是机器核实的结论 |
| `run.routed` | 审查分流来源 `{source:"review", target, findings}`；决定是否允许驳回 |
| `run.overrules` / `run.overruled` | 人的驳回记录（时间、状态、轮次、理由、发现标识）/ 已驳回发现的稳定标识集合 |
| `run.mode` | 普通轮为 null，根因调查轮为 `root_cause` |
| `run.failures[]` 新字段 | `attribution`（`implement/oracle/contract/base/environment`）、`epoch`（判据修复次数）、`mode`、`signature`；功能红层记录同时含各层结果与校验后的 `review`。连续性要求同一 epoch 的实际 `attempt` 相邻，且紧邻将调用的轮次；红层 review 的 `NEEDS_INPUT` 保留该轮证据但打断连续性，交还的轮次不打断，不另存 `progress` 计数 |
| 变更 `supersedes` | `{change_id, message, reason, blocker, source_branch, design_summary}`，指向旧变更 |
| 变更与旧运行 `superseded_by` | 新变更 ID；旧变更和运行状态均为 `SUPERSEDED` |
| `run.lessons_digest` | 合同提交上的 `openspec/lessons/` 树摘要，类型固定为字符串或 `null`；旧运行续跑时补算 |
| `run.lessons` | 复盘状态对象：`retrospective`、`signals`、`drafts`、`upgrade_suggested`、`error`；无信号、失败或成本上限均不改变 `PASSED` |
| `run.lessons_recurrence` | 本变更中“经验已提供但随后仍失败”的经验 ID 列表；跨变更次数另存 `.storyloop/state/lessons.json` |

`run --overrule` 必须给非空理由，只允许审查判定的 `ORACLE_DEFECT` 或 `NEEDS_SPEC_FIX`。对应发现改为 `implementation` 并重算归属；混合发现中剩余的判据问题仍会停下。实现前 RED 检查的判据错误不可驳回。

`change --supersede` 与 `--idea`、`--change` 互斥，必须有非空 `--message`。除 `NEEDS_SPEC_FIX` 外必须有 `--reason`；`--reason` 只用于 supersede。允许的旧运行状态为 `NEEDS_SPEC_FIX`、`BUDGET_EXHAUSTED`、`NO_PROGRESS`、`ORACLE_DEFECT`、`EXISTING_GREEN`、`NEEDS_INPUT`、`IMPLEMENTING`、`BOUNDARY_VIOLATION`、`CHARACTERIZATION_FAILED`。已合并的变更不允许作废。重复同一修订请求返回已有新草案，不重复创建；新运行必须从旧运行的当前源分支启动，不能从旧功能分支继续。

`acceptance-repair` 要求已有合同提交，允许的运行状态为 `CONTRACT_COMMITTED`、`IMPLEMENTING`、`BUDGET_EXHAUSTED`、`NEEDS_INPUT`、`EXISTING_GREEN`、`ORACLE_DEFECT`、`NO_PROGRESS`。其中 `CONTRACT_COMMITTED` 用于套件已提交、证明 RED 时因套件写入候选副本等副作用报 `BLOCKED` 的恢复；`PREPARING` 与 `PASSED` 仍禁止修复。修复套件提交后，引擎仍从合同提交重建候选副本并重新证明 RED，不跳过判据门。

### 错误码

命令失败时 stderr 输出一行 JSON：`{"status": <错误码>, "message": …, "next": …}`；可取得运行状态的 `run` / `approve` 错误还附 `stop`。`AGENT_UNAVAILABLE` 的停止报告不改变原运行状态及预算语义。

| 错误码 | 含义 |
|---|---|
| `BLOCKED` | 默认：前置条件不满足（未 init、状态不允许、校验失败、`openspec validate` 不通过等） |
| `NEEDS_INPUT` | manual 适配器在等人工回答 |
| `AGENT_UNAVAILABLE` | 代理进程没有给出回答（超时、限流、崩溃、CLI 不可用、进程树没能回收）。实现循环里出现时这一轮交还、不计入 `max_attempts`，记在 `run.agent_failures`；`run` 重试同一轮 |
| `AGENT_CONTRACT_INVALID` | 代理回答修复后仍不符合角色 schema（implement 计一轮失败，不会冒到这里） |
| `WORKSPACE_TAINTED` | 控制面、暂存区或工作区被意外改动；Git 不可见改动的消息最多列 20 条相对路径，Python `StoryLoopError.details.invisible[]` 保留全部 `{path, kind}`（CLI 错误 JSON 不新增该字段）；已有运行状态时保存停止报告，清理后 `reset` 再 `run` |
| `GIT_BLOCKED` / `GIT_CONFLICT` | Git 命令失败 / no-ff 合并失败（功能分支保留） |
| `UNSAFE_CONTAINMENT` | 子进程树没能在受控方式下回收 |

### 退出码

| 退出码 | 情形 |
|---|---|
| `0` | 正常结束，包括停在 `MANUAL_ACCEPTANCE_REQUIRED`（自动验收已全过，只是等人） |
| `2` | 返回状态属于 `FAILED`、`BLOCKED`、`NEEDS_INPUT`、`INTERRUPTED`、`UNSAFE_CONTAINMENT`、`WORKSPACE_TAINTED`、`BUDGET_EXHAUSTED`、`BASE_CHANGE_REQUIRED`、`BOUNDARY_VIOLATION`、`ERROR`、`CHARACTERIZATION_FAILED`、`EXISTING_GREEN`、`ORACLE_DEFECT`、`NEEDS_SPEC_FIX`、`NO_PROGRESS`、`SMOKE_UPDATE_FAILED`，或任何错误码 |
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
| `metrics.thresholds` | `{}` | 按 R1–R12 覆盖 `threshold`、`min_sample`；R8 也可覆盖 `low`。未知规则/字段、布尔、负数、非有限数值报错 |
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
- base 代理的独立 worktree 不是沙箱。command 适配器只把 cwd 和请求工作目录指向临时树，需使用代理 CLI 自带沙箱（例如 Codex `workspace-write`）或无凭证容器限制可写根；引擎不隔离 Git 配置、共享 `.git` 或同用户进程。base Codex 格式失败时作废尝试并新开受限调用，不用缺少 `-C`/沙箱选项的 `exec resume`。当前引擎源码由项目 Git 跟踪时，base 请选择 `--implementer human`；调用后源码摘要变化或无法计算只提醒核查及重新安装，不阻断运行或 reset。
- 代理进程只拿到白名单环境变量（`PATH`、`HOME`、`TEMP`、`TMP`、`JAVA_HOME`、`USERPROFILE` 等），名字含 KEY、TOKEN、PASSWORD、SECRET、CREDENTIAL 的一律剔除。
- `openspec/foundation/README.md` 存在时，除 survey 外每个角色的 payload 自动多一项 `foundation: {version, documents:[path]}`。
- 合同提交存在且经验层校验通过时，除 survey 和复盘外的角色还会收到 `lessons`：核心层带正文；普通层最多 10 条，只含 `id`、`summary`、`path`、`status`。没有 `openspec/lessons/` 时不注入该字段。

### 各角色的 payload 与返回

| 角色 | payload | 返回与引擎附加校验 |
|---|---|---|
| `product` | 新建 `{idea}`；修订 `{change_id, message, answers:[{question_id,question,answer}], current_product}` | 完整产品对象（见下）。ID 匹配 `[A-Za-z0-9][A-Za-z0-9_-]{0,79}` 且全局唯一；每个故事至少一条验收条件；`depends_on` 只能引用排在前面的故事；`recommended` 必须在 `options` 里 |
| `acceptance`（生成） | `{change, suite_root}` | `{files:[{path,content}], manifest}`。files 非空、路径相对且不是 CI、不能叫 `manifest.json`、单个 ≤2 MB；测试 ID 唯一；`criteria` 只能引用并必须覆盖全部 `automated` 条件；`timeout_seconds` 1–3600；`tier` 为 `fast`（缺省）或 `heavy`，须与条件的 `oracle_class` 对应；一个测试只能覆盖同一种实现前状态（应红或应绿）的条件 |
| `acceptance`（修复） | `{change: {…, repair:{reason, existing_files, manifest, failure_evidence}}, suite_root}`；`EXISTING_GREEN` 时 `failure_evidence` 为 `{status:"EXISTING_GREEN", criteria_green, results}` | 同上；返回里的 `manifest.json` 文件被忽略 |
| `acceptance`（冒烟维护） | `{mode:"smoke-maintenance", change, acceptance_suite, system_smoke, smoke_root}` | `{files, manifest:{version:1, tests:[{id,description,command,timeout_seconds}]}, rationale}`；测试非空、文件 ≤200 个 |
| `design` | `{change, answers, facts}` | `{summary, touched:[path], design, questions:[string], facts?}`。questions 非空停 `NEEDS_INPUT`；`design` 不能为空；`touched` 必须是合同提交里存在的文件 |
| `characterize` | `{change, test_roots, extend:[path], characterization, design}`（`characterization` 只在之前为 GREEN 时给，`design` 为方案或 null） | `{summary, touched, tests, questions:[]}`。只能写 `test_roots`（否则 `BOUNDARY_VIOLATION`，类别 `NOT_A_TEST_PATH`）；`touched` 必须是既有非测试文件；单元层必须全绿（否则 `CHARACTERIZATION_FAILED`） |
| `implement` | `{change, attempt, max_attempts, failures, answers, characterization, design_plan, facts, mode}`（`characterization`、`design_plan` 可为 null；`facts` 为列表，`mode` 为 null 或 `root_cause`） | `{summary, questions:[string], design, facts?}`。通过直接改工作区实现；questions 非空停 `NEEDS_INPUT`；结构不符计一轮失败 |
| `review` | `{change, attempt, layers, boundary, design_plan, answers, design, facts}` | `{verdict:"pass"|"fail"|"needs_input", summary, findings:[{message, perspective?, criterion_id?, evidence?, target?, location?, …}], questions, facts?}`；功能红层也会调用，纯环境错误不会调用；`pass` 不能带 findings 或 questions，也不能覆盖任何红层 |
| `survey` | `{baseline_version, foundation_root, required_documents, module_roots, integration_paths, reason}` | `{documents:[{path,content}]}`：1–40 份 `.md`、每份 ≤512 KB、八份必需文档齐全、不含配置里的密钥值或带口令的 URL |

survey 的说明由 `foundation-survey` 技能包生成：引擎把 SKILL.md 与 references 原文附在角色说明后（优先用项目 `.claude/skills/` 或 `.codex/skills/` 里的副本）。

普通角色返回可选 `lessons_used: [{id, how}]` 与 `errata: [{document, claim, correction, evidence:[path:line]}]`。`how` 最长 200 字符；未提供却声称使用的 ID 会进入诊断和报告，但不拒绝回答。勘误须指向现有 foundation 文档中的原文并给出可验证位置，不合格条目会被丢弃。两个字段进入严格 wire schema；内部业务校验器把它们当通用元数据处理，不要求各角色重复定义业务字段。

复盘调用使用 `review` 角色与 `payload.mode = "retrospective"`，只读执行，返回独立的严格 schema：`lessons`、`errata`、`uncovered_concepts`、`nothing_to_learn`。每次复盘最多接受三条经验提议；失败或达到成本上限只写复盘状态，不改变运行结论。

### 归属、事实与语义修复

review finding 可选 `target: implementation|oracle|contract`（缺省 implementation）及 `location: "path:line[:col]"`。非实现归属还须有合同内 `criterion_id` 和存在的安全源码行；oracle 只能指向当前验收套件或已锁定的特征化测试。`evidence` 可提供解释性说明，引擎不要求该字段非空。无效归属降回 implementation 并附 `attribution_note`，不会凭文字直接停到合同或判据修复。

分层验证存在功能失败时，引擎仍调用受只读保护的 review，并把原始层结果与校验后的 review 一并写入失败记录。review 返回 `needs_input` 时先保留该轮证据再暂停；没有有效 finding 或提问时，停止摘要显示实际失败层。

design、implement、review 可选返回 `facts: [{statement, location}]`（缺省空）；statement 最长 500 字符，location 必须定位真实安全源码行。引擎附角色与轮次后传给后续调用。supersede 的 product 请求包含 `{idea, message, current_product, supersede}`，其中 supersede 含旧变更 ID、理由、阻塞点、源分支、方案摘要、facts 与 evidence。新设计可收到 `prior_design` 与 `prior_facts`，明确标注旧变更来源，仅供参考。

产品、验收套件和设计方案通过 schema 却违反语义约束时，引擎最多另调同一角色一次，payload 增加 `previous_response`（原回答）和 `validation_errors`（错误文本列表）。第二次仍失败就停止；进程故障、安全错误不走此通道。诊断 `meta.json` 的 `semantic_repair` 标识该调用，和适配器的 JSON/schema 修复次数分开记录。

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

来源 `storyloop/role_schemas.py`，是两个 CLI 都能原生执行的严格子集：每个对象 `additionalProperties:false`、全部属性列入 `required`，引擎视为可选的字段声明为可为 `null`。引擎先丢弃可为 null 字段的 `null` 值再校验，所以省略可选字段同样合法；review 的 finding 允许额外键。`schema_for` 将 `lessons_used`、`errata` 合并进普通角色的顶层 schema，`wire_schema` 只移除 `x-` 开头的引擎内部约束标记，仍保持对象封闭和字段完整。

适配器应交给 CLI 原生约束（Codex `--output-schema`，Claude `--json-schema`）。不符时 Codex 适配器在同一会话带着具体问题修复（JSON 解析失败与结构不符都算，最多两次），仍不符报 `AGENT_CONTRACT_INVALID`。

### `_telemetry`（可选）

任何角色的返回对象里可以附带 `_telemetry`：数值键 `cost_usd`、`turns`、`duration_ms`、`input_tokens`、`output_tokens`、`segments`、`lanes`、`perspectives`，以及 `model`、`vendor`、`tier_applied`、`sessions`（会话 ID 列表）；画像能力回报见第六节。引擎剥离后记入账本：变更调用记在 `.storyloop/state/telemetry/<change-id>.json`，变更创建前的 product 调用先记在 `pending.json` 再收编，survey 记在 `foundation.json`。没有它时只记墙钟时间。

### 诊断记录

每次调用写 `.storyloop/state/telemetry/calls/<调用 ID>/`：`request.json`（脱敏）、`response.json` 或解析失败时的 `response.txt`、`stderr.log`、`meta.json`（结果、错误码、`schema_errors`、修复次数、会话 ID）。账本条目的 `call` 与 `session` 字段指向它。它们用于排障，不是证据。

账本条目新增 `lessons_provided`、`lessons_claimed`、`lessons_unknown` 三个 ID 列表，各自最多保存 20 项；任一原始列表超过上限时写 `lessons_truncated:true`。完整三项保存在对应调用的 `meta.json`。报告取证会在账本截断时回读并核对该完整记录；缺失或无效时明确标记证据不完整，不把截断摘要当完整事实。

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

## 六、角色画像（子项目 4）

`storyloop adapters check PROJECT [--json]` 发出 `{"protocol":"storyloop.adapter-capabilities.v1"}`，期望返回 `vendor`、`tiers: ["strong", "standard"]`、布尔 `subagents/path_guard/fast_checks`、`modes: ["*"]`。结果写 `.storyloop/state/adapters.json`，绑定配置及 argv 摘要；init/change 自动检查，run 在只读边界外刷新未知能力。已知映射缺档报 `ADAPTER_PROFILE_UNSUPPORTED`；未知旧适配器与 manual 能力缺失允许兼容执行，不能声称生效。跨厂商模式要求已知、不同 vendor，否则 fail_closed。

请求仍是 `storyloop.project-agent.v1`，只新增可选 `role_profile`：

```json
{"version":1,"tier":"strong","routing_tier":"standard","skills":[],
 "subagents":{"perspectives":["correctness","security","architecture","tests"],"explore":[],"lanes":false},
 "writable_paths":["app/**","tests/**"],"forbidden_paths":[],"fast_checks":[]}
```

`skills` 每项含 name/path/version/sha256，正文仍在 instructions。路径来自引擎边界规则；只读角色（包括 acceptance）无可写路径。`roles.<role>` 可覆盖 adapter/tier/fast_checks；判定角色降到 standard 必须提供 reason。tier 可按 light/standard/heavy 映射。`settings.stacks` 用于技能选择。

适配器 `_telemetry` 回报 model/vendor/tier_applied，可回报 `path_guard: enforced|unsupported` 与 `subagents_applied: {mode: parallel|sequential, perspectives, explore, lanes}`。engine 独立写 tier_requested/adapter/degraded，拒绝适配器覆盖。模型档次回报缺失或不匹配是“未生效”，不进入匹配档次统计；费用与失败调用仍保留在总账。第 3、5 节明确为适配器回报。profile 不存在时保留旧协议。

review 的新画像协议要求 findings 的 severity（P0/P1/P2/question）、confidence（0–100 整数）、introduced、trigger/impact/fix，以及 coverage。去向依次为老问题、out_of_scope、≥80 修复/归属停点、50–79 引用重点、低置信 P0/P1 人工确认、其余丢弃。可配置 `review_thresholds.fix/focus`。引擎生成独立 `F-ordinal-hash`，代理原 ID 留在 agent_id。第 2 节仅列 id/location/severity/confidence；正文与复核理由在第 4 节标 🤖。

只有已画像且请求 standard 的 review 高置信发现会触发 `mode: verify` strong 调用：请求 findings，返回 `results: [{id, confirmed, reason}]`，每个引擎 ID 恰好一次。strong 原审查不再复核；无画像的新发现记录 PROFILE_NOT_CONFIGURED。checklist 只核对已批准方案 selected option 的结构化 constraints（criterion_id/path/rule）按 canonical JSON 编码的 decision，恰好一行，status 为 pass/issue/unconfirmed；不从 Markdown 推断完整设计覆盖。语义不完整有一次补正机会。

调用 metadata 的 context 保存实际**提供**的底座、活规格、技能与方案文件路径/hash 和截断项，不代表代理实际读取。子代理视角由 profile 唯一决定；不支持并行时单会话依次处理。

`COST_LIMIT_REACHED` 是可恢复停点：limits 默认 max_calls=80、max_agent_wall_seconds=28800，max_tokens/max_cost_usd 默认为 null；旧配置同样获得默认挂死保护。每次调用前检查累计已用值，失败调用也计算；费用未知不估算，并展示 x/y 覆盖。提高 limits 再 run；已完成实现候选绑定指纹，在审查/复核停点恢复时复用，不重复实现。原始审查在复核费用停止时持久化。

`cross_vendor_review.on_unavailable` 默认 fail_closed：review 不可用停 AGENT_UNAVAILABLE、交还质量轮次；degrade 改用 implement 适配器审查并标记 degraded=cross_vendor。事后比较实际回报 vendor，不同仅代表回报匹配；同厂或缺失标“跨厂商未生效”，不改变证据型门。

设计审批停点之后接受 BASE 更新并合回功能分支时，旧方案的完整 `baseline_digest` 绑定失效；先执行 `replan` 重新生成方案，再 `approve --design`。启动前已冻结的纯经验目录提交可处于旧 BASE 之后，运行中未批准的源分支前进仍会被拒绝。

`approve --draft` 对 config 强度仅拒绝新建及从其他强度转换；允许既有 config 条目退休或保持该强度更新说明，实际配置不会新增或改动。
