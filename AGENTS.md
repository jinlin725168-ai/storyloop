# StoryLoop 仓库的代理须知

- 测试体系、套件分层与 CI 见 [docs/testing.md](docs/testing.md)。
- 改 StoryLoop 自身机制（重试、门禁、停点、分流、特征化）时，验收测试用 `storyloop.testing.nested.nested_case` 建嵌套项目、只替换相关角色，不要自己搭仓库或手写各角色回答；用法见 docs/testing.md 的“嵌套项目夹具”。
- 改运行主流程（`project_loop*`、`routing*`、特征化）的变更，提交前跑 `python tests/run.py --suite full`：受影响模块只按直接 import 选择，会漏掉间接依赖的端到端测试。
