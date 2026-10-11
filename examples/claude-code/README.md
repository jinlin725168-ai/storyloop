# 用 Claude Code 作为 StoryLoop 代理

`claude_agent.py` 是一个 `command` 适配器：从 stdin 读取 StoryLoop 请求，按角色以
`claude -p --restricted --output-format json --permission-prompts none --max-turns N --tools … --allowedTools …` 调用本机已登录的 Claude Code，把结果 JSON 写到 stdout。

角色与工具边界：

| 角色 | 工具 | 说明 |
|---|---|---|
| product / acceptance（旧名 smoke） | Read, Grep, Glob | 只读 |
| design / survey / review | 只读 + Bash（受限前缀） | 可运行命令，不可写文件 |
| implement / characterize | 只读 + Edit/Write/MultiEdit + Bash（受限前缀） | `--permission-mode acceptEdits` |

圈数上限是检查点而不是失败：每段最多 `--segment-turns`（默认 80）圈，到上限就用 `--resume` 续同一个会话继续，最多 `--max-segments`（默认 10）段（`--max-turns` 是 `--segment-turns` 的已弃用别名）；只有段数用尽才算失败，那说明代理在打转而不是活多。做得好不好由引擎的 `max_attempts` 判断。

Bash 默认只放行 `python3`、`python`、常见只读命令和只读 git（status/diff/log/show）；其他构建工具按项目追加，例如 `--bash "mvn:*"`（可重复）。Claude Code 对链式命令逐段匹配规则，所以提示词要求代理只跑原样单条命令：`git diff` 可以，`git -C x diff`、`git --no-pager diff`、带 `&&` 或管道的都会被拒。`--guide FILE`（可重复）把项目规范附进每次提示，`--model` 选模型，`--dry-run` 只打印将要执行的 argv。

review 角色默认展开为多视角子代理：正确性、安全、架构合规（对照 design_plan 与知识库）、测试质量四个并行会话看同一份证据，各自只报能指到文件或证据的 finding；再由一个主评审会话合并成最终 verdict，丢弃子视角的 finding 必须在 summary 里说明理由。`--review-perspectives correctness,security`（子集）或 `none`（单会话）可调，`--review-parallel`（默认 4）控制并发。引擎协议不变，只多了 `_telemetry.perspectives`。

implement 角色可以按层拆子代理：`--implement-lanes backend=backend/ruoyi-modules/,frontend=frontend/src/,sql=sql/biz/` 声明每条通道拥有的路径前缀。有 design_plan 时，主实现者会话先只读地把方案拆成通道任务（少于两条就退回单会话），通道会话并行实现各自的层（提示词限定只改本通道前缀下的文件、不跑全量回归），然后同一个主实现者会话 `--resume` 回来做集成：对齐层间接口、跑构建与单元/集成命令、修集成问题、写含全部通道 Tests First 的 design 记录。`--implement-parallel`（默认 3）控制通道并发。引擎协议不变，遥测多了 `lanes`。

请求带 `response_schema` 时，最终会话以 `--json-schema` 运行并以信封里的 `structured_output` 为答案，结构由 CLI 保证；多视角审查与按层实现的中间会话使用适配器自带的 schema。

返回对象里附带 `_telemetry`（费用、轮数、时长、token、段数、模型、会话 ID 列表 `sessions`），引擎剥离后记入成本账本并汇总到验收报告；会话 ID 可用 `claude --resume <id>` 回看。
就地构建产生的 `target/`、`node_modules/` 等目录需要在 `storyloop init --settings` 的 `scratch_paths` 里声明，否则会判 WORKSPACE_TAINTED；不想声明时用 `examples/tools/scratch_build.py` 在临时目录构建。

启用方式（`storyloop init` 之后编辑 `.storyloop/config.json`）：

```json
{
  "adapter": "command",
  "agent_command": ["{python}", "/abs/path/storyloop/examples/claude-code/claude_agent.py", "--bash", "mvn:*"]
}
```

已在 Linux（WSL2）上用一个最小 Python 仓库完整跑通：init → change → approve → 变更验收 RED → 实现 → 回归 → 独立审查 → no-ff 合并 → 报告 → `storyloop ci`。之后以 RuoYi-Vue-Plus monorepo（Spring Boot + Vue 3）作为底座跑过三个真实变更，其中一个全栈变更（供应商分类）的账单是 13 次代理调用、代理累计 27 分钟、13.5 USD、2 轮实现尝试。

RuoYi 底座上验证过的配置（`agent_timeout_seconds` 14400、`max_attempts` 4）：

```json
{
  "adapter": "command",
  "agent_command": ["{python}", "/abs/path/storyloop/examples/claude-code/claude_agent.py",
                    "--bash", "mvn:*", "--bash", "pnpm:*", "--bash", "npx:*",
                    "--guide", "/abs/path/my-project/acceptance/AGENT_GUIDE.md"]
}
```
