# 用 Claude Code 作为 StoryLoop 代理

`claude_agent.py` 是一个 `command` 适配器：从 stdin 读取 StoryLoop 请求，按角色以
`claude -p --restricted --output-format json` 调用本机已登录的 Claude Code，把结果 JSON 写到 stdout。

角色与工具边界：

| 角色 | 工具 | 说明 |
|---|---|---|
| product / smoke | Read, Grep, Glob | 只读 |
| review | 只读 + Bash（受限前缀） | 可运行测试，不可写文件 |
| implement | 只读 + Edit/Write + Bash（受限前缀） | `--permission-mode acceptEdits` |

圈数上限是检查点而不是失败：每段最多 `--segment-turns`（默认 80）圈，到上限就用 `--resume` 续同一个会话继续，最多 `--max-segments`（默认 10）段；只有段数用尽才算失败，那说明代理在打转而不是活多。做得好不好由引擎的 `max_attempts` 判断。

Bash 默认只放行 `python3`、`python`、常见只读命令和只读 git（status/diff/log/show）；其他构建工具按项目追加，例如 `--bash "mvn:*"`。`--guide FILE` 把项目规范附进每次提示。
就地构建产生的 `target/`、`node_modules/` 等目录需要在 `storyloop init --settings` 的 `scratch_paths` 里声明，否则会判 WORKSPACE_TAINTED；不想声明时用 `examples/tools/scratch_build.py` 在临时目录构建。

启用方式（`storyloop init` 之后编辑 `.storyloop/config.json`）：

```json
{
  "adapter": "command",
  "agent_command": ["{python}", "/abs/path/storyloop/examples/claude-code/claude_agent.py", "--bash", "mvn:*"]
}
```

已在 Linux（WSL2）上用一个最小 Python 仓库完整跑通：init → change → approve → 冒烟 RED → 实现 → 回归 → 独立审查 → no-ff 合并 → 报告 → `storyloop ci`。
