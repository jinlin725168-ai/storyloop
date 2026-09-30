# 排障 FAQ

按停点和现象组织。每条给出原因和处理方式；处理方式里的命令都在 [使用指南](product-iteration-guide.md) 有完整说明。

## 状态与停点

**先看哪里**
`storyloop log PATH --change ID`：每次代理调用的结果、耗时、费用、会话 ID 与记录目录，每轮失败卡在哪一层，最后一行是下一步命令。每个停点的返回和 `status --json` 也都带 `next`。

**`WORKSPACE_TAINTED`，但我什么都没改**
常见原因：残留的 dev server（vite 会重写 `frontend/src/types/auto-imports.d.ts`、`components.d.ts`）；代理就地构建产生了未声明的被忽略目录（`target`、`logs`、`.flattened-pom.xml`）；代理执行了 `git add`。处理：停掉所有 dev server，把合法的产物加进 `scratch_paths`（改 settings 需要一次 BASE 迭代，或在没有变更时重新 init），然后 `storyloop reset --reason "…"` 再 `run`。

**`NEEDS_INPUT` 反复出现同一个问题**
代理没看到答案。检查 `.storyloop/state/answers/<id>.json` 是否记录；适配器把 `payload.answers` 原样传给了模型。指南文件里明确写"questions 只用于真实产品歧义，工具和权限问题不要提问"能显著减少无意义提问。

**`BASE_CHANGE_REQUIRED` 但我认为那是业务代码**
看提案里的路径分类：`FOUNDATION_CHANGE` 是 init 时锁定的已跟踪文件，`INTEGRATION` 是构建清单。要么把该目录加进 `module_roots`（BASE 迭代改 settings 后重新 init 只在没有变更时可行），要么按提案走一次独立底座提交。

**`BUDGET_EXHAUSTED`，失败原因都是同一个用例**
先看 `.storyloop/evidence/<id>/smoke-result.json` 的 stderr。若是用例缺陷（严格模式定位器匹配到两个元素、加载遮罩过渡、时钟回拨导致雪花 ID 报错），走 `acceptance-repair`；若是实现问题，修订合同或提高 `max_attempts`。

**`MANUAL_ACCEPTANCE_REQUIRED` 后 `run` 报"Workspace changed"**
人工核对时本地启动了应用并生成了文件，或者改了代码。清理后 `reset`；如果代码确实改了，需要重新走实现（reset 后 run 会重新验证）。

**完成阶段崩溃（报告、合并、回执中途报错）**
直接再 `run`。完成阶段幂等：已存在的实现提交被采纳，引擎产物丢弃后重新生成。如果报"Smoke evidence is not bound"，说明工作区有非引擎产物的改动，先 `git status` 看。

## 冒烟与候选副本

**RED 阶段报环境错误而不是红灯**
候选副本环境只有 PATH 和临时目录变量，执行器不能依赖当前 shell 的环境变量；依赖必须离线可用（Maven `-o`、已安装的 `node_modules`）。执行器要区分退出码 1（行为失败）和 2（环境错误），只有 1 是有效 RED。

**冒烟报"candidate modified"**
用例或执行器写入了候选副本（日志、数据库文件、构建产物）。执行器应把候选副本同步到 scratch 再构建；用例的临时文件放系统临时目录。

**名字敏感的配置文件没进候选副本**
`.env*`、`*.key`、`*.pem`、`credentials.json`、`secrets.yaml` 默认排除。确实是已跟踪配置的加进 `candidate_allow`；真正的密钥不要进仓库。

**变更验收里的浏览器用例第一次总是失败**
写盲测时定位器猜错很常见。让acceptance 角色优先写接口用例，浏览器用例只覆盖核心链路，并在指南里指向 `acceptance/ui/<capability>/` 的页面事实。失败后用 `acceptance-repair`。

**`survey` 报 "copies a configured secret"**
摸底文档里出现了配置文件中 password/secret/token 类键的实际值，或带口令的连接串。原始返回保存在 `.storyloop/state/foundation/last-survey.json`，把对应值改成"见 `<文件>` 的 `<键>`"这样的描述后 `storyloop survey PATH --from <该文件>` 安装，不用重新摸底。

## 适配器

**Claude 适配器报 `error_max_turns` 后失败**
默认每段 80 圈、最多 10 段自动续会话。10 段都用完说明代理在打转：`storyloop log` 找到这次调用的记录目录，看 `stderr.log` 最后一段在做什么，或用 `meta.json` 里的会话 ID `claude --resume <id>` 回看；通常是权限被拒后反复重试同一条命令。

**代理说"git diff 被拒绝"**
Bash 规则按前缀逐段匹配：`git -C x diff`、`git --no-pager diff`、带管道或 `&&` 的都不在白名单。指南里写明"原样单条命令"。

**Codex 适配器返回非 JSON**
角色结构已由 `--output-schema` 原生约束，很少发生。仍发生时引擎用 `codex exec resume` 在同一会话里让它补出 JSON（结构不符时附上具体问题），最多两次。仍失败时 `storyloop log` 看该次调用的 `response.txt`、`schema_errors` 与 `stderr.log`。

**`AGENT_CONTRACT_INVALID`**
代理的最终回答在修复后仍不符合角色的 JSON Schema。implement 角色的这种失败计为一轮失败尝试并回喂给下一轮；其他角色会中断运行，`storyloop log` 查看后直接 `run` 续跑。自定义 `command` 适配器应读取请求里的 `response_schema` 并交给自己的 CLI 约束。

**`python -m storyloop.cli run` 什么都不做**
入口是 `storyloop` 或 `python -m storyloop`。

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
没有任何变更时：`rm -rf .storyloop` 后重新 init。已有变更后：`base-propose` → 提交 → `base-update`。
