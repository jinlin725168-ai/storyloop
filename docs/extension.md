# 扩展引擎

对应引擎版本：0.1.0，核对日期 2026-10-11。这一页回答“我要给引擎加 X，要动哪些文件”。每种扩展都先写一个能复现失败的测试（见 [testing.md](testing.md)），并遵守第七节的不变量。

## 一、新增一个 CLI 命令

| 步骤 | 文件 | 做什么 |
|---|---|---|
| 1 | `storyloop/cli.py` 的 `parser()` | 在 `descriptions` 加中文说明；在下面的 `if/elif` 里加参数，需要变更 ID 的用 `dest="change_id"` |
| 2 | `storyloop/cli.py` 的 `dispatch()` 或 `_product_dispatch()` | 不需要已初始化仓库的命令放 `dispatch()`；其他放 `_product_dispatch()`（已统一检查 `.storyloop/baseline.json`） |
| 3 | 业务模块 | 逻辑写在对应层的模块里（见 [modules.md](modules.md)），`cli` 只做参数和显示 |
| 4 | 返回值 | 返回 `{"status": ..., "change_id": ..., "next": ...}`；`display()` 会打印这些键 |
| 5 | `main()` 的 `failures` 集合 | 如果新状态表示失败或等人，加进去，退出码才会是 2 |
| 6 | 文档 | README 命令表、[使用指南](product-iteration-guide.md)、[api.md](api.md) 的命令一节 |

## 二、新增一个停点（运行状态）

| 步骤 | 文件 | 做什么 |
|---|---|---|
| 1 | `storyloop/project_loop.py` | 用 `_pause(project, state, "NEW_STATUS", **details)` 停下，状态写进 `.storyloop/state/runs/<id>.json` |
| 2 | `_open_run()` 的 `resumable` | 能用 `run` 续跑的状态加进去，否则续跑会报 `Run state cannot be resumed` |
| 3 | `storyloop/next_steps.py` | 给出可复制的下一步命令 |
| 4 | `storyloop/cli.py` 的 `failures` | 需要人处理的停点退出码为 2 |
| 5 | 文档 | [api.md](api.md) 的状态表、[FAQ](faq.md) 按停点组织的一节、[使用指南](product-iteration-guide.md) |

## 三、新增一个角色

| 步骤 | 文件 | 做什么 |
|---|---|---|
| 1 | `storyloop/project_agents.py` | `ROLES` 加名字；`INSTRUCTIONS` 写角色说明（写给代理看，含 `FOUNDATION_NOTE` 与精确的返回形状）；Codex 沙箱在 `_codex()` 里按角色选 `read-only` 或 `workspace-write` |
| 2 | `storyloop/role_schemas.py` | `SCHEMAS` 加返回结构，遵守严格子集（对象封闭、全部 required、可选字段用 `_nullable`） |
| 3 | `storyloop/project_loop.py` | `LoopServices` 加可选字段（默认 `None` 时该环节跳过并记录原因）；`_default_services()` 接上 `invoke_project_agent`；调用包在 `_read_only_call` 或 `_write_safely` 里 |
| 4 | `examples/claude-code/claude_agent.py` | `ROLE_TOOLS` 给出工具集，`SCHEMA_HINTS` 给出返回形状提示；写入型角色才给 `Edit/Write` |
| 5 | 文档与决策 | [角色目录](roles.md)、[api.md](api.md) 的角色表；按权限边界新增角色需要一条决策记录（参考 [0016](decisions/0016-roles-by-authority.md)） |

只是“更细的分工”（多视角审查、按层并行实现）不加角色，放进适配器内部的子会话，引擎协议不变。

## 四、新增一个 settings 键或配置键

两类配置，入口不同：

- **settings（工程画像，`init --settings` 或 BASE 迭代时决定）**：`storyloop/project_profile.py` 的 `detect_project()` 给默认值，`validate_project_settings()` 的 `allowed` 集合加键并写校验（路径用 `_relative_paths`，命令用 `_commands`）。值落在 `.storyloop/config.json` 的 `profile` 里。
- **运行参数（`.storyloop/config.json` 顶层，人直接编辑）**：`storyloop/baseline.py` 的 `bootstrap_project()` 写默认值；读取处用 `config.get(key, 默认值)` 兼容老仓库（参考 `design_min_stories`）。

两类都要更新 [接入手册](onboarding-a-repository.md) 的配置说明和 [api.md](api.md) 的配置表。

## 五、新增一层验证

| 步骤 | 文件 | 做什么 |
|---|---|---|
| 1 | `storyloop/project_loop.py` | `LAYER_ORDER`、`_run_checks_safely()` 加一层，必须用 `run_*_isolated`；`layers_green()` 决定它是否允许 `SKIPPED` |
| 2 | `storyloop/change_report.py` | `LAYERS` 与中文名表；报告要求每层都有结论 |
| 3 | `storyloop/change_log.py` | `LAYER_NAMES` |
| 4 | 命令来源 | 来自 settings 的命令层还要改 `project_profile.LAYERS` 与 `layer_commands()` |
| 5 | 文档与决策 | [决策 0015](decisions/0015-test-layers.md) 的取代记录、概念手册的测试分层 |

## 六、新增一个技能包

1. 在 `storyloop/skills/<name>/` 放 `SKILL.md`，frontmatter 必须有 `name`、`version`、`description`；参考资料放 `references/`。
2. 改内容就升 `version`：`storyloop skills` 靠它判断项目里的副本是否过期。
3. 角色需要嵌入技能时，在 `project_agents.SKILL_ROLES` 登记；技能文本原样进入代理请求，写给代理看。
4. `pyproject.toml` 的 `package-data` 已包含 `skills/**/*.md`，其他扩展名需要补。

## 七、禁止事项（不变量）

改动触及以下任何一条，都必须有测试证明它仍然成立：

1. 没有观察到有效 RED（候选副本里行为断言失败，退出码 1）就不调用实现代理；环境错误不是 RED。
2. 合同、变更验收套件、系统冒烟、证据文件的字节由指纹保护，篡改即失败。
3. 实现代理不能改 `acceptance/changes/<id>/`、`acceptance/smoke/`、`openspec/changes/<id>/` 的合同部分和报告。
4. 验收与回归在隔离候选副本里跑，写入副本即失败。
5. 边界审计覆盖全树，未声明的被忽略文件、暂存、控制面改动判 `WORKSPACE_TAINTED`。
6. 引擎不 push、不删分支、不重写历史；CI 文件只能由人经 `base-propose --allow-ci` 进入底座。
7. 报告只接受真实命令结果和人工记录，代理的文字不是证据。
8. 完成阶段幂等：重复 `run` 不重复调用代理、不重复提交。
9. 任何合同修订让旧批准失效；人工验收记录与实现指纹绑定。
10. 特征化门：开启时，实现不能修改未被 `test_roots` 内绿测试锁定的既有文件；系统冒烟只经人 `smoke-apply` 更新。
11. 不引入第三方运行时依赖；不再引入按实例目录或 Stage 配置工作的代码路径（旧模式存档于本地 tag `archive/legacy-modes`）。
