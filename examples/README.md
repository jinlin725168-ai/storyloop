# Examples

| 目录 | 用途 |
|---|---|
| `todo-demo/` | 入门教程（[docs/tutorial.md](../docs/tutorial.md)）用的最小 Python 仓库：复制到临时目录、`git init` 后即可按教程跑通一次闭环 |
| `claude-code/` | 驱动 Claude Code（`claude -p`）的 `command` 适配器 |
| `tools/` | 验收执行器的积木（`smoke_harness.py`）与前端构建走 scratch 的示例（`scratch_build.py`） |
| `generic-project/` | 通用工程模式的最小命令序列说明 |

无需模型的完整闭环示例在 `tests/test_product_e2e.py`：它创建临时 Git 工程，锁定底座，生成外部冒烟测试，观察真实 RED，写入业务模块，生成追溯报告并验证本地 `--no-ff` 合并。
