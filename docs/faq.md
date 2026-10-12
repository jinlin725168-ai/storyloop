# 排障 FAQ

按停点和现象组织。每条给出原因和处理方式；处理方式里的命令都在 [使用指南](product-iteration-guide.md) 有完整说明。

## 状态与停点

**先看哪里**
`storyloop log PATH --change ID`：每次代理调用的结果、耗时、费用、会话 ID 与记录目录，每轮失败卡在哪一层，最后一行是下一步命令。每个停点的返回和 `status --json` 也都带 `next`。

**`WORKSPACE_TAINTED`，但我什么都没改**
常见原因：残留的 dev server（vite 会重写 `frontend/src/types/auto-imports.d.ts`、`components.d.ts`）；代理就地构建产生了未声明的被忽略目录（`target`、`logs`、`.flattened-pom.xml`）；代理执行了 `git add`。另一个原因是代理超时或出错时已经改动了工作区。处理：停掉所有 dev server，把合法的产物加进 `.storyloop/config.json` 的 `profile.scratch_paths`（本地控制面，每次调用现读，不需要 BASE 迭代），然后 `storyloop reset --reason "…"` 再 `run`。如果 `status --json` 的 `run.status` 不是 `WORKSPACE_TAINTED`（问题出在 RED、方案或特征化阶段，只是报错），清理后直接 `run`，`reset` 会被拒绝。

**`NEEDS_INPUT` 反复出现同一个问题**
代理没看到答案。检查 `.storyloop/state/answers/<id>.json` 是否记录；适配器把 `payload.answers` 原样传给了模型。指南文件里明确写"questions 只用于真实产品歧义，工具和权限问题不要提问"能显著减少无意义提问。

**`BASE_CHANGE_REQUIRED` 但我认为那是业务代码**
看提案里的路径分类：`FOUNDATION_CHANGE` 是 init 时锁定的已跟踪文件，`INTEGRATION_CHANGE` 是构建清单，`CI_PROTECTED` 是 CI 配置。`module_roots` 存在 `baseline.json` 里，要改只能在没有进行中变更时重新 init；否则按提案走一次独立底座提交。

**`BUDGET_EXHAUSTED`，失败原因都是同一个用例**
先 `storyloop log` 或看 `.storyloop/evidence/<id>/acceptance-result.json` 的 stderr。若是用例缺陷（严格模式定位器匹配到两个元素、加载遮罩过渡、时钟回拨导致雪花 ID 报错），走 `acceptance-repair`；若是实现问题，提高 `.storyloop/config.json` 的 `max_attempts` 后 `run` 续跑，或另开新变更。运行开始后合同不能再修订。

**`MANUAL_ACCEPTANCE_REQUIRED` 后 `run` 报"Workspace changed"**
人工核对时本地启动了应用并生成了文件，或者改了代码。把工作区恢复到暂停时的样子后直接 `run`（只比较实现指纹）。只有代码确实要改时才 `reset`：它会让下一次 `run` 重跑一轮实现，人工验收也要重做。

**完成阶段崩溃（报告、合并、回执中途报错）**
直接再 `run`。完成阶段幂等：已存在的实现提交被采纳，引擎产物丢弃后重新生成。如果报"Smoke evidence is not bound"，说明工作区有非引擎产物的改动，先 `git status` 看。

<a id="characterization_failed"></a>
**`CHARACTERIZATION_FAILED`**
characterize 角色写的特征化测试在旧代码上没跑绿（`unit_commands` 整层都要绿，不只是新测试）。`storyloop log` 或 `status --json` 的 `run.unit` 看哪条断言错了，就地修正 `test_roots` 里的这些测试（不用提交）后 `run`：引擎不再调用代理，重跑整个单元层，绿了才提交这些测试并继续。测试断言了将要改变的行为时，把它改成只锁定"必须不变"的部分（扩展的列表用 contains）。修正只能改测试文件。

**`BOUNDARY_VIOLATION`**
代理写了不允许的路径：受保护的合同或验收套件、可变区外的新文件、链接，或特征化写了 `test_roots` 以外的文件（类别 `NOT_A_TEST_PATH`）。`storyloop log` 找到是哪次调用写的，还原这些文件后 `run`；确实需要扩大可写范围就走 BASE 迭代。它不能用 `acceptance-repair` 处理。

**`EXISTING_GREEN`：RED 阶段验收套件已经是绿的**
变更验收套件在实现之前就通过了，全部或部分条件（`run.acceptance.criteria_green` 列出没能失败的条件）。要么合同描述的行为其实已经存在，要么用例没有断言新行为。先确认需求是否已经实现：已实现就放弃这个变更；用例太弱就 `storyloop acceptance-repair PATH --change ID --reason "…"`，acceptance 角色拿到这次的结果加强断言，从合同提交重新证明 RED 后 `run` 继续。不修复直接 `run` 只会再停在这里。

**`GIT_CONFLICT`：完成阶段 no-ff 合并失败**
功能分支被保留，工作区可能停在合并中途。`git merge --abort` 恢复源分支，`git status` 弄清原因后再 `run`。

**`GIT_BLOCKED` / `UNSAFE_CONTAINMENT`**
Git 命令或子进程没能在受控方式下完成（超时、输出超限、进程树没能回收）。`storyloop log` 看最近一次调用；Linux 上确认 `/proc` 可读、内核支持 child subreaper。

**manual 适配器：命令报 `NEEDS_INPUT` 并给出请求文件路径**
这是在等人工回答，不是失败。读 `.storyloop/requests/project-<role>.json`，把 `{request_id, result}` 写到 `.storyloop/responses/project-<role>.json` 后重跑原命令。

## 冒烟与候选副本

**RED 阶段报环境错误而不是红灯**
候选副本环境只有 `PATH`、`SYSTEMROOT`、`TEMP`、`TMP`（外加 `PYTHONDONTWRITEBYTECODE`、`PYTHONIOENCODING`），执行器不能依赖当前 shell 的环境变量；依赖必须离线可用（Maven `-o`、已安装的 `node_modules`）。执行器要区分退出码 1（行为失败）和其他非零（环境错误，约定用 2），只有 1 是有效 RED。

**冒烟报"candidate modified"**
用例或执行器写入了候选副本（日志、数据库文件、构建产物）。执行器应把候选副本同步到 scratch 再构建；用例的临时文件放系统临时目录。

**名字敏感的配置文件没进候选副本**
`.env*`、`*.key`、`*.pem`、`credentials.json`、`secrets.yaml` 这类文件出现在候选副本里会直接阻断。确实是已跟踪配置的加进 `candidate_allow`；真正的密钥不要进仓库。

**变更验收里的浏览器用例第一次总是失败**
写盲测时定位器猜错很常见。让 acceptance 角色优先写接口用例，浏览器用例只覆盖核心链路，并在指南里指向 `acceptance/ui/<capability>/` 的页面事实。失败后用 `acceptance-repair`。

**`survey` 报 "copies a configured secret"**
摸底文档里出现了配置文件中 password/secret/token 类键的实际值，或带口令的连接串。原始返回保存在 `.storyloop/state/foundation/last-survey.json`，把对应值改成"见 `<文件>` 的 `<键>`"这样的描述后 `storyloop survey PATH --from <该文件>` 安装，不用重新摸底。

## 适配器

**Claude 适配器报 `error_max_turns` 后失败**
默认每段 80 圈、最多 10 段自动续会话。10 段都用完说明代理在打转：`storyloop log` 找到这次调用的记录目录，看 `stderr.log` 最后一段在做什么，或用 `meta.json` 里的会话 ID `claude --resume <id>` 回看；通常是权限被拒后反复重试同一条命令。

**代理说"git diff 被拒绝"**
Bash 规则按前缀逐段匹配：`git -C x diff`、`git --no-pager diff`、带管道或 `&&` 的都不在白名单。指南里写明"原样单条命令"。

**Codex 适配器返回非 JSON**
角色结构已由 `--output-schema` 原生约束，很少发生。仍发生时引擎用 `codex exec resume` 在同一会话里让它补出 JSON（结构不符时附上具体问题），最多两次。仍失败时 `storyloop log` 看该次调用的 `response.txt`、`schema_errors` 与 `stderr.log`。

**`AGENT_UNAVAILABLE`：代理进程没有给出回答**
超时、限流、崩溃、CLI 不可用都归这一类。它不是质量问题，所以实现循环里出现时这一轮不计入 `max_attempts`，记在 `status --json` 的 `run.agent_failures` 里。`storyloop log` 找到该次调用的 `stderr.log` 看原因，处理后 `run` 会重试同一轮。如果代理在出错前已经改了工作区，会判 `WORKSPACE_TAINTED`，走 `reset`（同样不计预算）。

**`AGENT_CONTRACT_INVALID`**
代理的最终回答在修复后仍不符合角色的 JSON Schema。implement 角色的这种失败计为一轮失败尝试并回喂给下一轮；其他角色会中断运行，`storyloop log` 查看后直接 `run` 续跑。自定义 `command` 适配器应读取请求里的 `response_schema` 并交给自己的 CLI 约束。

## 环境

**Windows 上回归被标记 `SKIPPED`**
`.cmd/.bat/.ps1` 包装器不会被执行。用 settings 指定原生可执行程序或 `{python}` 解释器 argv。

**Playwright 浏览器下载失败**
设置 `PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright` 后再 `npx playwright install chromium`。

**WSL2 下 pnpm 命中了 Windows 的 shim**
把原生 pnpm 链接到 `~/.local/bin/pnpm` 并放在 PATH 前面。

**端口 80 或 8080 被占**
`.env.development` 的 `VITE_APP_PORT` 用 5173，后端 `server.port` 用 8081；冒烟执行器自己找空闲端口，不受影响。

**MySQL 导入中文乱码**
导入时加 `--default-character-set=utf8mb4`。

**`base-update` 报回归快照不一致**
有并发写入者（通常是残留的 vite）。停掉后重跑。

**init 之后想改底座文件**
`storyloop base-propose PATH --path <文件> --reason "…"` → 在源分支单独提交这些路径 → `storyloop base-update PATH --proposal BASE-ID`。不需要删 `.storyloop` 重新 init。
