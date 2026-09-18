# 用 Claude Code 作为 StoryLoop 代理

`claude_agent.py` 是一个 `command` 适配器：从 stdin 读取 StoryLoop 请求，按角色以
`claude -p --restricted --output-format json` 调用本机已登录的 Claude Code，把结果 JSON 写到 stdout。

角色与工具边界：

| 角色 | 工具 | 说明 |
|---|---|---|
| product / smoke | Read, Grep, Glob | 只读 |
| review | 只读 + Bash（受限前缀） | 可运行测试，不可写文件 |
| implement | 只读 + Edit/Write + Bash（受限前缀） | `--permission-mode acceptEdits` |

Bash 默认只放行 `python3`、`python` 和常见只读命令；其他构建工具按项目追加，例如 `--bash "mvn:*"`。
不放行 `git`：`git status` 会改写 `.git/index`，StoryLoop 会判定为 WORKSPACE_TAINTED。

启用方式（`storyloop init` 之后编辑 `.storyloop/config.json`）：

```json
{
  "adapter": "command",
  "agent_command": ["{python}", "/abs/path/storyloop/examples/claude-code/claude_agent.py", "--bash", "mvn:*"]
}
```

已在 Linux（WSL2）上用一个最小 Python 仓库完整跑通：init → change → approve → 冒烟 RED → 实现 → 回归 → 独立审查 → no-ff 合并 → 报告 → `storyloop ci`。
