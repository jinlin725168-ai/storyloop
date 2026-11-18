# StoryLoop 仓库的代理须知

- 测试体系、套件分层与 CI 见 [docs/testing.md](docs/testing.md)。
- 改 StoryLoop 自身机制（重试、门禁、停点、分流、特征化）时，验收测试用 `storyloop.testing.nested.nested_case` 建嵌套项目、只替换相关角色，不要自己搭仓库或手写各角色回答；用法见 docs/testing.md 的“嵌套项目夹具”。
- 改运行主流程（`project_loop*`、`routing*`、特征化）的变更，提交前跑 `python tests/run.py --suite full`：受影响模块只按直接 import 选择，会漏掉间接依赖的端到端测试。
- 改 `storyloop/skills/*` 下的技能要升版本；新版本在真实运行（L3 或自迭代）中通过后，在 `evals/skills/<技能>/<版本>.json` 记录内容摘要、用例结论与证据，否则发布门把它标为 pending；沿用旧结果会让 core 失败。见 docs/testing.md 的“技能发布门”。
