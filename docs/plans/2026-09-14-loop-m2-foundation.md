# StoryLoop M2 通用底座实施契约

> 状态：已完成
> 范围确认：2026-09-14
> 依据：`AI-Loop开发体系设计文档-最终版.md` 与现有 StoryLoop v1 实现

## Epic：阶段化 Loop Runner 通用底座

### 不可变需求

1. 保留 StoryLoop v1 产品故事工作流和现有配置兼容性，不以重写换架构。
2. 新增依赖无关的 v2 Loop 配置契约，覆盖 Agent 注册与运行时选择、阶段依赖、OpenSpec 输入、写入边界、保护边界、分级裁判及全局预算。
3. Runner 只能依据外部 Judge 的真实结果推进状态；Agent 的文字声明不能产生 PASSED。
4. Red Gate 必须在实现写入前执行；测试锁定后，任何实现尝试都不得修改测试或其他保护输入。
5. 实现进程只能修改当前 Stage 的 `write_paths`；触碰 `protected_paths`、架构路径或边界外文件必须阻断并保存证据。
6. Agent 适配器通过统一协议注册；运行时覆盖优先级固定为 CLI、`LOOP_AGENT`、默认值，并且只探测本次选中的适配器。
7. 重试、重复失败、退避与总时长必须有硬上限；预算跨恢复持久化，不能通过重启绕过。
8. 架构、协议或底座变更必须返回机器可识别的 `ARCHITECTURE_DECISION_REQUIRED`，交由人工决策。
9. 所有命令使用 argv 数组执行，不依赖 Bash、`.sh` 或 shell 拼接；Windows、Linux、macOS 使用同一核心协议。
10. 不新增运行时第三方依赖；L0 的 JDK/Spring、Admin、Android 底座只通过 specs、paths、judges 和 adapter 契约接入。

### 成功标准

- [x] v1 的 44 个基线测试及后续新增回归测试全部通过。
- [x] v2 配置对未知字段、重复 ID、无效依赖、依赖环、危险路径、写入/保护范围重叠和敏感环境变量全部拒绝。
- [x] Codex、Claude/command、manual 等适配器可通过同一注册协议选择，只探测选中项。
- [x] Stage 按依赖顺序执行，并产生 Red、L1、L2、final judge 的可审计证据。
- [x] 边界外写入、保护输入篡改、无效 RED、错误/跳过测试及预算耗尽均不能被判定为成功。
- [x] 相同失败达到阈值后进入 replan；失败证据和上游 handoff 会注入下一轮。
- [x] README、协议与 Mermaid 架构图说明 v1/v2 的职责、迁移方式和安全边界。

### 禁止模式

- 禁止破坏 v1 配置和现有 CLI（兼容性：已有实例必须可继续恢复）。
- 禁止 `shell=True` 或命令字符串拼接（安全性与跨平台性）。
- 禁止把整个项目目录作为默认可写范围（最小权限）。
- 禁止允许 Agent 修改测试、规格、配置、控制状态或证据（防止改考卷）。
- 禁止根据 Agent 总结或退出码单独宣告成功（裁判必须提供完整命名结果）。
- 禁止把密钥、Token、密码、凭证类环境变量加入透传白名单（数据保护）。
- 禁止用 TODO、空实现或恒真测试满足核心流程（完整性与测试有效性）。
- 禁止自动安装依赖、修改系统配置、访问生产数据库、提交、推送或部署。

### 选定方案

采用“兼容层上的增量 v2”：v1 的 product/check/runner 保持工作，新建严格的阶段配置与策略模块，随后让新的 Stage Runner 复用既有进程执行、指纹、证据、锁和错误模型。这样能保住当前已验证能力，同时把设计文档要求的阶段、边界、适配器和预算逐层接入。

未采用直接重写 v1：一次替换会把已验证的证据、恢复和人工验收语义同时置于风险中。除非 v1 合同明确废弃，否则不重新考虑。

未采用复制原 when 引擎：当前工程目标是独立、标准库、跨平台，直接搬运会引入隐含目录、Git、Bash 和业务依赖。除非获得原引擎的稳定协议与跨平台测试，否则不重新考虑。

### 设计发现

- 当前 `models.py` 已提供严格字段校验与安全错误模型，可复用其 `fields`、`identifier`、`require`、`strings`。
- 当前 `processutil.py` 已使用 argv 和受限环境执行子进程，可作为 Judge 与 adapter 的统一执行底座。
- 当前 `storage.py` 已有原子 JSON、进程锁、受控输入与工作区指纹。
- 当前 `runner.py` 已实现外部证据、时间预算、重复失败和独立 QA，但粒度仍是 story，尚无 stage/path/adapter registry。
- 目标设计的 `.sh`、`./loop`、`python3` 示例不能直接作为跨平台契约；核心层统一使用 argv，平台脚本由 adapter 显式解释执行。

## Task 1：v2 配置与策略契约（已完成）

### 目标与工作量

预计 4～6 小时。交付一个独立、无副作用的 v2 合同模块；本任务不启动 Agent、不执行 Judge、不修改 v1 Runner。

### 实施步骤

1. 在 `tests/test_stage_config.py` 添加真实失败测试：合法配置、未知字段、适配器选择优先级、未知适配器、敏感环境变量、重复 ID、未知依赖、依赖环、危险路径、写入/保护重叠、缺失 Red/L1/L2 Judge、预算边界。
2. 运行该测试并确认因 `storyloop.stage_config` 不存在而 RED，而不是语法或环境错误。
3. 新建 `storyloop/stage_config.py`，实现 `validate_loop_config(value)`、`select_adapter(config, requested=None, environ=None)` 与 `stage_order(config)`。
4. 使用严格字段集合、POSIX 相对路径模式、显式 DAG 检查和确定性拓扑排序；错误统一抛出 `StoryLoopError`，不返回部分配置。
5. 运行新增测试至 GREEN，再运行全部单元测试确认 v1 无回归。
6. 检查新文件行数、函数长度、TODO/空实现、敏感字段和 `shell=True`，完成重构后再次运行全量测试。

### v2 配置形状

```json
{
  "version": 2,
  "agent": {
    "default": "codex",
    "adapters": {
      "codex": {"kind": "codex", "command": []},
      "claude": {"kind": "command", "command": ["claude"]}
    },
    "timeout_seconds": 300,
    "environment_allowlist": ["PATH", "JAVA_HOME"]
  },
  "budgets": {
    "attempts_per_stage": 5,
    "total_seconds": 1800,
    "repeated_failure_limit": 2,
    "backoff_seconds": [2, 5, 10, 20]
  },
  "stages": [{
    "id": "feature-example",
    "depends_on": [],
    "specs": ["openspec/changes/example/proposal.md"],
    "write_paths": ["service/business/**"],
    "protected_paths": ["acceptance/**", "openspec/**"],
    "judges": [
      {"id": "red", "phase": "red", "command": ["python", "acceptance/check.py"]},
      {"id": "core", "phase": "l1", "command": ["python", "acceptance/check.py", "core"]},
      {"id": "full", "phase": "l2", "command": ["python", "acceptance/check.py", "full"]}
    ]
  }]
}
```

### SRE 细化结果

- 粒度：单一合同模块，可独立完成，不超过一个工作日。
- 可实施性：函数、文件、数据形状、错误策略与验证命令明确。
- 可测标准：至少 12 个有行为意义的测试；每个测试对应一种会导致越界、误判或不可恢复的真实缺陷。
- 依赖：仅依赖现有 `models.py`；不依赖后续 Stage Runner。
- 安全：拒绝绝对路径、反斜杠、`..`、保留控制目录、写入/保护重叠和敏感环境变量。
- 边界：空列表、Unicode ID、重复项、超大数、依赖环、未知 adapter、空命令、路径 glob 前缀重叠均有明确结果。
- 红旗：无占位文本、无 TODO、无条件式步骤、无安装依赖。
- 测试有效性：断言具体异常或确定顺序，而非只检查对象存在。

### 完成标准

- [x] `tests/test_stage_config.py` 的 12 个场景全部通过。
- [x] `python -m unittest discover -s tests -v`：56/56 通过，退出码为 0。
- [x] v1 配置与 CLI 行为未改变。
- [x] 新生产文件 222 行，所有函数不超过 50 行。
- [x] 生产代码中无 TODO、空实现、裸异常捕获、`shell=True` 或敏感值。


## Task 2：Stage 文件边界快照与越界阻断（已完成）

### 目标与工作量

预计 4～6 小时。提供不依赖 Git 的确定性文件快照和差异校验，使后续 Agent 调用只能改当前 Stage 的 `write_paths`，且不能改动 `protected_paths`。本任务不执行 Agent、不实现 Judge 生命周期。

### 实施步骤

1. 在 `tests/test_boundaries.py` 先添加失败测试：允许范围内的新建/修改/删除、边界外写入、保护文件修改/删除、符号链接或目录联接、Windows 分隔符归一化、构建缓存忽略、并发快照差异与文件数量上限。
2. 运行新增测试并确认因 `storyloop.boundaries` 不存在而 RED。
3. 新建 `storyloop/boundaries.py`，实现不可变 `WorkspaceSnapshot`、`capture_snapshot(root)`、`verify_stage_changes(before, after, stage)` 和 POSIX glob 匹配。
4. 快照只记录相对路径、文件类型、大小与 SHA-256；不读取凭证类文件，不跟随符号链接或目录联接，不把文件内容写入状态或错误消息。
5. 差异校验分别报告新增、修改、删除；任一保护路径变化或任一不匹配 `write_paths` 的变化都抛出安全的 `StoryLoopError`。
6. 运行新增测试至 GREEN，再执行全量测试、编译和静态禁项扫描。

### SRE 细化结果

- 粒度：单一安全组件，可独立完成，不超过一个工作日。
- 可实施性：输入是项目根、前后快照和已验证 stage；输出为确定性变更清单或 fail-closed 异常。
- 可测标准：至少 10 个行为测试，明确覆盖新建、修改、删除、越界、保护路径和链接攻击。
- 依赖：只依赖标准库、现有 `storage.IGNORED`、`models.StoryLoopError` 及 Task 1 的 stage 合同。
- 安全：不跟随链接、不读取敏感文件、不记录内容、不信任绝对路径、不因缓存文件产生误报。
- 边界：空工作区、Unicode 文件名、大文件、同名大小写、文件在快照间消失、并发新增、超过文件上限都必须有确定结果。
- 红旗：禁止自动删除或回滚用户文件；检测到越界后仅阻断并把结构化变更交给上层保存证据。
- 测试有效性：每个测试都验证具体变化分类或错误，不使用只检查对象存在的恒真断言。

### 禁止模式

- 禁止依赖 `git diff`（Stage Runner 必须能在未初始化 Git 的可信本地项目运行）。
- 禁止跟随 symlink/junction，或在错误中输出文件内容。
- 禁止忽略普通隐藏源文件；只忽略固定的构建、依赖缓存和控制运行目录。
- 禁止把越界变化自动删除或覆盖（避免破坏用户数据）。
- 禁止用路径字符串前缀代替组件级 glob 匹配（避免 `src/app` 误匹配 `src/application`）。

### 完成标准

- [x] `tests/test_boundaries.py`：21/21 通过，覆盖新增、修改、删除、保护路径、越界、硬链接、symlink/reparse、大小写与扫描中并发变化。
- [x] `python -m unittest discover -s tests -v`：78/78 通过；`compileall` 退出码为 0。
- [x] 保护路径及边界外的新增、修改、删除全部被阻断。
- [x] 合法写入返回按路径排序且不含文件内容的变更清单。
- [x] 新生产文件小于 500 行，单函数不超过 50 行；敏感字面量扫描命中文件数为 0。
- [x] 独立复审在明确模块合同内无 Critical/Important 阻断项。

### 安全合同与后续硬前置

`boundaries` 是 writer 已停止后的检测原语，不是 OS 沙箱。双扫描会拒绝扫描窗口内观察到的新增、删除和原子替换，但任何有限扫描都不能阻止最后一次扫描后的外部写入。Stage Runner 调用它之前必须证明 Agent 及其后代进程已经全部终止；受信任工作区之外还必须使用低权限隔离环境。Windows 文件读取通过 `CreateFileW(FILE_FLAG_OPEN_REPARSE_POINT)` 打开对象本身，并在读取前拒绝 reparse point，避免短暂读取链接目标。


## Task 3：封闭式进程执行与工作区静默保证（已完成）

### 目标与结果

为 v2 Stage Runner 建立可审计的进程树清理结果。Windows 提供内核级 Job Object containment；POSIX 保留 process group 协作清理以兼容 v1，但因 `setsid()` 可逃逸，明确返回 `contained=false`，不得进入 v2 边界快照阶段。

### 实现摘要

1. Windows 创建带 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 的 Job Object。
2. 可信 bootstrap 先阻塞在无缓冲单字节门闩；Runner 成功分配 Job 后才由 feeder 放行目标命令，消除“先执行、后分配”的竞态。
3. 正常退出、非零退出、超时、输出超限与异常中断统一终止进程树并回收 feeder。
4. `run_process` 新增 `contained` 字段；Windows 只有全部清理成功才为 `true`，POSIX 永远不虚报安全 containment。
5. 清理后再次计算输出大小；kill、wait、Job close 和线程启动失败均 fail-closed，且不会因单步异常跳过其余清理。

### SRE 结果

- 粒度：单一进程执行组件，未混入 Adapter、Judge 或 Stage 状态机。
- 可测标准：真实延迟 child/grandchild writer 覆盖成功、非零、超时和输出超限；异常分支使用聚焦单测。
- 依赖：仅 Python 标准库与 Win32 Kernel32，无第三方运行时依赖。
- 安全：Windows 禁止 breakaway，目标在进入 Job 前不能执行；POSIX 能力不足时诚实 fail-closed。
- 边界：stdin Unicode、父进程先退出、孙进程、feeder 启动失败、fallback kill 失败及最终输出竞态均有确定结果。
- 红旗：未使用 `taskkill` PID 追踪、第三次快照、`shell=True` 或命令字符串拼接。
- 测试有效性：真实等待超过 writer 延迟并验证文件不存在，不用 mock 调用次数替代核心行为证据。

### 完成标准

- [x] Windows 正常退出、非零退出、超时和输出超限均无后代 writer 在 Runner 返回后继续写入。
- [x] Windows Job Object 有真实系统测试；POSIX 平台适配明确返回 `contained=false`，不虚报安全保证。
- [x] v1 `run_process` 行为保持兼容，新增字段为向后兼容扩展。
- [x] `contained` 只有安全清理成立时为真；Task 4/Stage Runner 必须显式检查该字段。
- [x] `python -m unittest discover -s tests -v`：89/89 通过；编译和静态安全检查通过。
- [x] 独立复审确认原 1 个 Critical、3 个 Important 全部关闭，无新阻断项。

### 已知 Low 加固项

- `WindowsJob.assign()` 的临时 process HANDLE 关闭失败可增加诊断；`close()` 可保留失败 HANDLE 供重试。
- `_abort_unassigned` 的多步清理失败目前聚合为安全错误，后续可增加不含路径和内容的结构化诊断。


## Task 4：v2 Adapter 注册、选中项探测与统一调用（已完成）

### 目标与工作量

预计 4～8 小时。新增独立的 v2 Adapter 运行模块，消费 Task 1 的 registry/选择结果与 Task 3 的 containment 结果；只探测并调用本次选中的 Adapter。Task 4 不实现 Stage DAG、Red/L1/L2 Judge 生命周期或重试状态机。

### 实施步骤

1. 在 `tests/test_stage_agents.py` 先写失败测试，覆盖 CLI/环境/默认选择、只探测选中项、manual/command/codex、严格 JSON 协议和错误分类。
2. 新建 `storyloop/stage_agents.py`，提供 `probe_selected_adapter(...)` 与 `invoke_selected_adapter(...)`；复用 `stage_config.select_adapter`，禁止自行实现第二套优先级。
3. probe 仅解析或检查选中 Adapter 的可执行入口；未选中的无效 Adapter 不得启动、联网或产生错误。
4. 外部 Adapter 通过 argv 和结构化 stdin 接收 `loop.agent.v2` 请求；输出必须是单个严格对象，未知字段、乱码、超限、非零退出和空输出全部阻断。
5. 调用 `run_process` 后必须先验证 `result["contained"] is True`，再解析响应或允许上层采集 Task 2 快照；POSIX 当前必须安全拒绝外部 Adapter。
6. 环境透传只允许 v2 配置中的 `environment_allowlist`，并继续拒绝密钥、Token、密码和凭证类变量；日志与错误不得包含值。
7. manual Adapter 不启动进程，使用请求 ID 绑定响应；旧响应、错角色或错 Stage 不得复用。
8. Adapter 返回架构/协议/底座变更请求时，规范化为机器可识别的 `ARCHITECTURE_DECISION_REQUIRED`，不得直接修改受保护路径。
9. 新测试 GREEN 后运行全仓测试、编译、静态安全扫描和独立复审。

### SRE 细化结果

- 粒度：单一 Adapter 边界，预计不超过一个工作日；Stage orchestration 留给后续任务。
- 可实施性：输入为已验证 v2 config、选择覆盖、role/stage/payload；输出为严格响应或 `StoryLoopError` 状态。
- 可测标准：至少覆盖 selected-only probe、containment false、timeout、truncated、nonzero、malformed、manual request binding 和 architecture escalation。
- 依赖：依赖 Task 1 `stage_config`、Task 3 `run_process`、现有错误/原子 JSON 工具；不依赖未来 Stage Runner。
- 安全：不执行未选 Adapter，不传敏感环境变量，不接受 Agent 自报成功替代 Judge，不在错误中回显 stdout/stderr。
- 边界：命令不存在、argv 含 Unicode、空 payload、未知 role、旧 manual response、POSIX 无安全 containment 均有确定结果。
- 红旗：禁止 `shell=True`、全环境继承、探测所有注册项、自动安装 CLI、网络探测和根据退出码单独宣告业务成功。
- 测试有效性：验证实际未调用未选项，验证 containment 门在 JSON 解析前发生，避免只断言函数返回非空。

### 完成标准

- [x] CLI、`LOOP_AGENT`、默认值优先级沿用 Task 1，且只探测选中 Adapter。
- [x] command/codex/manual 共享 `loop.agent.v2` 请求与严格响应合同。
- [x] 外部进程 `contained` 非真时 fail-closed，不能进入快照或 Judge 阶段。
- [x] 敏感环境值不透传、不记录；架构升级返回机器状态。
- [x] v1 行为保持兼容，全仓测试、编译、静态检查和独立复审通过。
### 实施结果

- 新增 `storyloop/stage_agents.py` 与 `tests/test_stage_agents.py`；`StoryLoopError.details` 为向后兼容扩展。
- command、Codex、manual 使用同一严格协议；Agent 不能通过响应字段替代 Judge 宣告通过。
- 外部调用先检查 Windows containment；POSIX 外部 Adapter 按既定合同安全拒绝。
- manual 持久控制面与 Codex 临时响应均移出项目树，关闭 Agent 写域内的链接替换与控制面污染。
- Task 4 专属测试 16/16、全仓 105/105、编译和静态安全检查通过；独立复审无 Critical/Important。


## Task 5：v2 Judge 执行边界与标准结果合同（已完成）

### 目标与工作量

预计 4～8 小时。新增单个 Judge 的安全执行模块，把 Stage 配置中的 `red/l1/l2/final` argv 转换为可验证的 `loop.judge.v2` 证据。Task 5 不实现 DAG 调度、Agent 重试或整段 Stage 状态机。

### 实施步骤

1. 在 `tests/test_stage_judges.py` 先写失败测试，覆盖 Judge 精确选择、RED 的有效失败、pass/fail/error/skip、超时、截断、非零退出、containment 和副作用。
2. 新建 `storyloop/stage_judges.py`，提供 `run_stage_judge(project, config, stage_id, judge_id, context)`；复用 v2 config，不自行解释第二套 Stage/phase 规则。
3. Judge command 只按 argv 执行，入口解析为原生可执行文件或显式解释器；禁止 shell、自动安装、网络探测和全环境继承。
4. Judge 输出必须是单个严格 `loop.judge.v2` JSON：绑定 stage/judge/phase/request_id，拒绝未知/重复字段、非有限数、乱码和超限。
5. `run_process` 返回后先要求 `contained is True`，再解析 stdout；POSIX 保持 fail-closed。
6. Judge 前后使用 Task 2 快照，拒绝非忽略文件的任何变化；Judge 只有读权限语义，不能修改实现、规格或控制面。
7. RED 只接受结构化 `fail` 作为有效红灯；`error`、`skip`、协议错误、环境失败和已有 `pass` 均返回不同机器状态，禁止把环境坏当成 RED。
8. 生成只含哈希、标识、状态和安全摘要的内存证据；持久化、预算扣减与 Stage 转移留给后续 Runner 任务。
9. 专属测试 GREEN 后运行全仓测试、编译、静态安全扫描和独立复审。

### SRE 细化结果

- 粒度：一次只执行一个已配置 Judge，预计一个工作日内完成；不混入 DAG/重试状态机。
- 输入/输出：输入为已验证 config、stage_id、judge_id、有限 JSON context；输出为标准 Judge 结果或机器可识别的 `StoryLoopError`。
- 可测标准：至少覆盖准确选择、真实 RED、existing-green、error/skip、containment-before-parse、无副作用和 request binding。
- 依赖：Task 1 配置、Task 2 快照、Task 3 containment；不依赖 Task 4 Agent 响应内容。
- 安全：Judge 视为受信任测试命令，但仍禁止敏感环境、shell 和写副作用；错误不得回显 stdout/stderr 或文件内容。
- 边界：未知 stage/judge、同 phase 多 Judge、Unicode argv/context、空 findings、超深/超大 JSON、Windows/POSIX 差异均有确定结果。
- 红旗：不得仅按退出码判业务状态，不得允许 Agent 伪造 Judge 结果，不得在 Judge 内修改快照基线或控制状态。
- 测试有效性：至少一条真实子进程协议测试和一条真实副作用检测；其余错误矩阵可用受控 mock，避免全是 mock。

### 完成标准

- [x] 一个配置 Judge 映射一个严格、可绑定、可分类的 `loop.judge.v2` 结果。
- [x] RED 的 fail 与环境/协议 error、skip、existing-green 明确区分。
- [x] containment 检查早于输出解析，Judge 前后无非忽略文件变化。
- [x] 无 shell、无敏感环境透传、无 stdout/stderr/文件内容泄漏。
- [x] v1 与 Task 1～4 行为兼容，全仓验证和独立复审通过。
### 实施结果

- 新增 `storyloop/json_contract.py`、`storyloop/stage_judges.py` 和 `tests/test_stage_judges.py`；Task 4 Agent 同步复用公共严格 JSON 合同。
- 单 Judge 请求绑定执行前工作区摘要和解析后命令摘要；状态与退出码双重校验，Agent 不能伪造 Judge verdict。
- readonly 快照覆盖普通源码、`.storyloop`、普通 `.git/`、目录和可移植元数据；真实子进程写 `.git/config` 或只改文件权限均被阻断。
- linked Git worktree/submodule 在 Judge 启动前 fail-closed；硬链接对象仓库明确安全拒绝，不生成假 GREEN。
- Task 5 专属测试 14/14、全仓 119/119、编译与静态规模检查通过；独立复审 Critical 0、Important 0。


## Task 6：单 Stage 单次闭环编排（已完成）

### 目标与工作量

预计 6～10 小时。新增一次 Stage attempt 的内存编排器，把 Task 2/4/5 串成 `RED 或 EXISTING_GREEN → Agent（必要时）→ L1 → L2` 的最小闭环。Task 6 不实现 DAG 调度、跨运行持久化、预算重试或 final Judge。

### 实施步骤

1. 在 `tests/test_stage_runner.py` 先写失败测试，覆盖真实 RED、已有 GREEN、Agent 修改边界、L1/L2 顺序、Judge 失败、非终态与架构升级。
2. 新建 `storyloop/stage_runner.py`，提供 `run_stage_attempt(project, config, stage_id, payload, requested_adapter=None, environ=None)`；只编排一个已验证 Stage。
3. 精确执行该 Stage 唯一 red Judge。`RED` 才进入实现；`EXISTING_GREEN` 不得破坏已有实现，也不调用 Agent，直接进入验证链。
4. RED 分支在 Agent 前后采集适合实现写入的快照；调用 Task 4 后使用 `verify_stage_attempt_changes`，只允许 `write_paths`，拒绝 protected/out-of-bound/control/Git/缓存变化。
5. Agent 的 `ok` 只表示调用完成，不能改变 Stage 结论；`NEEDS_INPUT` 和 `ARCHITECTURE_DECISION_REQUIRED` 原样传播，并且不能误跑后续 Judge。
6. 按声明顺序执行全部 L1，再执行全部 L2；任一 `FAIL/ERROR/SKIP` 立即停止，后续 Judge 不运行。全部 GREEN 才返回 `STAGE_GREEN`。
7. 结果只保存在内存，包含 Stage、是否调用 Agent、允许的文件变化以及 red/L1/L2 evidence；不持久化 payload、stdout/stderr 或文件内容。
8. 对实现快照增加控制面覆盖策略，确保 `.storyloop`、普通 `.git/` 和元数据变化不能借默认忽略规则绕过，同时不把正常父目录 mtime 误判为越界。
9. 专属测试 GREEN 后运行全仓测试、编译、静态安全扫描和独立复审。

### SRE 细化结果

- 粒度：一个 Stage、至多一个 Agent 实现调用和一轮 L1/L2，预计一个工作日；DAG/重试/持久化拆到后续任务。
- 输入/输出：输入为已验证 config、stage_id、有限 payload 和 Adapter 选择；输出为 `STAGE_GREEN` 或明确的机器错误状态。
- 可测标准：至少覆盖 RED→Agent→L1→L2、EXISTING_GREEN→L1→L2、越界写、protected 写、L1 短路、L2 短路、manual needs-input 和 architecture escalation。
- 依赖：Task 2 boundaries、Task 4 stage_agents、Task 5 stage_judges；不得复制它们的协议或安全检查。
- 安全：Agent 停止且 containment 成立后才采集快照；Judge 结论不能由 Agent response 替代；错误与结果不持久化敏感 payload。
- 边界：未知 Stage、没有 L1/L2、多个同 phase Judge、零文件变化、只改元数据、linked Git、Unicode payload/路径均有确定行为。
- 红旗：禁止自动改测试/规格、existing-green 人工制造 RED、失败后继续跑 Judge、在本任务内引入重试循环或 DAG 状态。
- 测试有效性：至少一条真实 Agent 写文件并通过真实 Judge 的系统链；Mock 只用于分支顺序和不可达断言。

### 完成标准

- [x] 单 Stage 能完成可证明的 RED/已有 GREEN 分支和 L1/L2 验证链。
- [x] Agent 只能修改 write_paths，任何 protected、控制面、缓存目录或越界变化均阻断。
- [x] Judge 短路顺序确定，只有全部 L1/L2 GREEN 才产生 `STAGE_GREEN`。
- [x] 非终态/架构升级原样传播且不会误执行后续步骤。
- [x] v1 与 Task 1～5 行为兼容；全仓验证和独立复审通过。

### 实施结果

- 新增 `storyloop/stage_runner.py` 与 `tests/test_stage_runner.py`；单次 attempt 严格执行 RED/已有 GREEN、可选 Agent、全部 L1、全部 L2。
- Agent 快照覆盖项目全树及目录安全元数据，不沿用 Judge 的构建缓存忽略策略；通配写边界只允许本次真实匹配后代所需的新祖先目录。
- 正常、非终态、协议失败、进程失败和安全 cleanup 后的任意异常都会复核写边界；不安全 cleanup 使用 `UNSAFE_CONTAINMENT`，禁止后快照与 Judge。
- Windows Job 增加成员清零等待，关闭 TerminateJobObject 异步返回造成的 cwd 句柄泄漏；真实测试证明超时返回后工作目录可立即重命名。
- Task 6 专属测试 13/13、containment 16/16、全仓 137/137；编译、函数/文件规模检查通过；独立复审 Critical 0、Important 0。


## Task 7：v2 持久状态、预算与可恢复重试（已完成）

### 目标与工作量

预计 6～10 小时。在 Task 6 的单次 Stage attempt 外增加一个可跨进程恢复的单 Stage 生命周期，落实 `attempts_per_stage`、`total_seconds`、`repeated_failure_limit` 和 `backoff_seconds`。Task 7 不实现 Stage DAG、多 Stage 并行或 final Judge。

### 实施步骤

1. 在 `tests/test_stage_state.py` 先写失败测试，覆盖首次运行、进程崩溃恢复、尝试/时间预算、重复失败指纹、退避、非终态、状态损坏与并发 Runner。
2. 新建版本化 `loop.state.v2` 状态模块；状态放在项目同级 v2 控制目录，使用原子替换和 OS 锁，不进入 Agent 写域。
3. 状态绑定规范化项目身份、配置摘要和 Stage ID；只保存 payload 摘要，不持久化 payload、stdout/stderr、文件内容或敏感环境值。
4. 在调用 Agent 前原子登记 attempt 并保守预留时间；正常结束后按实际耗时结算，崩溃恢复保留预留消耗，防止重启绕过预算。
5. 对 Judge/Agent 的安全机器结果计算失败指纹；同一指纹达到 `repeated_failure_limit` 时停止自动重试，不能靠变化文案规避。
6. 按 attempt 索引选择并持久化 `backoff_seconds` 与下一可运行时间；时钟回拨、状态缺失或非法时间值必须保守阻断。
7. `NEEDS_INPUT`、`ARCHITECTURE_DECISION_REQUIRED` 和 `UNSAFE_CONTAINMENT` 持久化为不同状态；前两者可在新输入后恢复，后者必须人工确认执行环境安全后才可继续。
8. manual 回执采用可恢复确认顺序：Runner 状态成功落盘后才消费完成回执，避免“回执已删、状态未写”的崩溃窗口。
9. 只循环当前 Stage，调用 Task 6 的一次 attempt；达到 GREEN、预算耗尽、重复失败、非终态或安全错误即停止，不引入 DAG 调度。
10. 专属测试 GREEN 后运行全仓测试、编译、静态安全扫描和独立复审。

### SRE 细化结果

- 粒度：单 Stage 的持久生命周期与预算控制，预计一个工作日；DAG/final 留给后续任务。
- 输入/输出：输入为已验证 config、Stage、有限 payload 和当前控制状态；输出为 `STAGE_GREEN`、可恢复等待或明确终止原因。
- 可测标准：崩溃点覆盖“计费后未调用、Agent 完成未结算、manual 回执未确认”；并发启动只能有一个持锁 Runner。
- 依赖：Task 1 budgets、Task 4 control root/manual 协议、Task 6 single attempt、现有原子 JSON/锁工具；不得另建重复配置解释器。
- 安全：先记账后执行；状态损坏、身份不匹配、时钟异常和不安全 containment 均 fail-closed；持久证据不含敏感内容。
- 边界：零退避、最后一次 attempt、existing-green、重复与非重复失败、人工输入恢复、配置变化均有确定迁移。
- 红旗：禁止无限重试、sleep 持锁、失败文案直接作指纹、消费 manual 回执后再落盘、状态损坏时静默重置。
- 测试有效性：使用可控时钟和故障注入验证写前/写后崩溃窗口；至少一条跨两次 Runner 调用的真实恢复测试。

### 完成标准

- [x] 尝试、总时间、重复失败和退避预算跨进程不可绕过。
- [x] 状态写入原子、单 Runner 互斥、损坏或身份不匹配时安全阻断。
- [x] 非终态与 manual 回执可恢复，不丢请求、不重复消费成功回执。
- [x] 单 Stage 达到 GREEN 后状态和证据可重载验证，且不持久化敏感内容。
- [x] v1 与 Task 1～6 行为兼容，全仓验证和独立复审通过。

### 当前进度

- [x] 批次 1：`loop.state.v2` 外部状态协议、项目/配置/Stage/payload 摘要绑定、严格字段校验与项目外 OS 锁。
- [x] 批次 2：attempt 预记账、总时间结算、失败指纹、重复失败和退避迁移。
- [x] 批次 3：非终态恢复、manual 两阶段确认、真实跨运行恢复与最终复审。
- 当前验证：Task 7 聚焦测试 101 项全部通过（1 项因当前 Windows 无符号链接权限按能力跳过）；全仓 174 项执行为 OK（同一项跳过）；编译、函数/文件规模及生产代码安全静态扫描通过；独立复审 Critical 0、Important 0，结论 Ready。
