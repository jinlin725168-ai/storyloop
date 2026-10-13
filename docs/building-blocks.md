# 可复用积木

对应引擎版本：0.1.0，核对日期 2026-10-11。改引擎之前先查这里：下面每一项都已经处理过安全、跨平台和崩溃恢复的细节，重写一遍通常会丢掉其中一项。

## 一、引擎内部

| 积木 | 路径 | 用途 | 典型调用 |
|---|---|---|---|
| `StoryLoopError` / `require` | `storyloop/models.py` | 唯一的业务错误类型，带机器可读 `code`（默认 `BLOCKED`）；CLI 把它变成退出码 2 和下一步提示 | `require(cond, "人能看懂的原因")`；需要特定停点时 `raise StoryLoopError(msg, code="WORKSPACE_TAINTED")` |
| `text` / `identifier` / `strings` / `fields` | `storyloop/models.py` | 输入校验：非空文本（≤20000 字符）、安全 ID、字符串列表、对象键集合 | `identifier(change_id, "change id")` |
| `write_json` / `write_text_atomic` / `load_json` | `storyloop/storage.py` | 原子写（临时文件 + fsync + replace）、大小上限、读错时只报文件名 | `write_json(path, value, max_bytes=1_000_000)` |
| `locked` / `locked_file` | `storyloop/storage.py` | 操作系统级互斥锁，进程崩溃由内核释放 | `with locked(project): ...`（`run_change` 用它保证一个仓库一次只跑一个 run） |
| `repository_relative` / `safe_target` | `storyloop/path_security.py` | 仓库相对路径规范化，拒绝 `..`、盘符、UNC、链接与重解析点祖先 | 任何来自配置或代理的路径先过 `safe_target(project, rel, "label")` |
| `run_process` | `storyloop/processutil.py` | 无 shell、argv 形式、最小环境、时间与输出上限、整棵进程树回收；返回 `contained` 等字段而不是抛异常 | 调用后检查 `contained is True`、`timed_out`、`truncated`、`returncode` |
| `redact` | `storyloop/processutil.py` | 落盘或显示前脱敏常见凭证格式 | 写日志、报错、诊断记录前统一调用 |
| `_git` | `storyloop/baseline.py` | 所有 Git 命令的入口：禁用 hooks、最小环境、30 秒上限，失败抛 `GIT_BLOCKED` | `_git(project, "rev-parse", "HEAD")` |
| `start_feature` / `commit_paths` / `finish_feature` | `storyloop/project_git.py` | 建 `feature/<id>` 分支、只暂存显式路径并 `--no-verify` 提交、切回源分支 `--no-ff` 合并 | 引擎的每个提交都走 `commit_paths(project, paths, "type(id): subject")` |
| `capture_snapshot` / `snapshot_changes` | `storyloop/boundaries.py` | 写入者停止后的全树快照与差异，拒绝链接、硬链接、采集中变化 | 包住一次代理调用：前后各拍一次，比较 |
| `audit_project_changes` | `storyloop/project_boundaries.py` | 把 Git 可见改动分类（可变区 / 集成点 / 底座 / CI / 违规）并在需要时开 BASE 提案 | 每轮实现后、验证后 |
| `write_safely` / `read_only_call` | `storyloop/project_loop_guards.py` | 代理调用的护栏：控制面、暂存区不变；只读调用不能改树（scratch 与允许的证据除外） | 新增一个会调用代理的环节时把调用包进去 |
| `visible_paths` | `storyloop/smoke_runner.py` | 当前存在的 Git 可见文件（已删除的已跟踪文件不算） | 构造候选副本或算指纹时取文件清单，不要直接用 `git ls-files -co` |
| `count_tests` | `storyloop/oracle_counts.py` | 从测试工具的完整输出统计执行数 | 新的验证层要自证时用它，在输出截断之前调用 |
| `run_smoke_isolated` / `run_commands_isolated` | `storyloop/smoke_runner.py` | 在 Git 可见文件构成的临时候选副本里跑套件或回归命令，绑定输入指纹 | 新增一层验证时用它，不要在工作树里直接跑 |
| `implementation_digest` | `storyloop/smoke_runner.py` | “人验收的那份代码”的指纹：Git 可见文件减去引擎产物 | 人工记录、证据与它绑定 |
| `layer_commands` / `matches_test_root` | `storyloop/project_profile.py` | 取某层的回归命令（兼容旧 `test_commands`）；判断路径是否测试文件（目录或 `**`/`*` glob） | |
| `invoke_project_agent` | `storyloop/project_agents.py` | 调用一个角色：加 foundation、带 schema、选适配器、修复结构、写诊断记录与账本 | 编排层通过 `LoopServices` 间接调用 |
| `schema_for` / `schema_errors` / `normalize` | `storyloop/role_schemas.py` | 角色返回结构的单一来源 | 新角色先在这里加 schema |
| `next_step` | `storyloop/next_steps.py` | 停点或错误码 → 可复制的下一步命令 | 新增停点时同步加一条 |
| `record_call` / `summarize` | `storyloop/telemetry.py` | 账本写入与按变更、按角色汇总 | 报告成本节、`status`、`log` 都读它 |
| `open_call` / `close_call` | `storyloop/call_log.py` | 每次代理调用的诊断目录 | 只由 `invoke_project_agent` 调用 |

## 二、适配器与目标仓库用的积木

| 积木 | 路径 | 用途 |
|---|---|---|
| Claude Code 适配器 | `examples/claude-code/claude_agent.py` | 驱动 `claude -p`：按角色收窄工具、Bash 前缀白名单、项目规范文件、圈数到上限续会话、review 多视角子代理、implement 分通道、回报成本 |
| `smoke_harness.py` | `examples/tools/smoke_harness.py` | 验收执行器积木（无依赖，复制到执行器旁边用）：把候选副本同步到 scratch（`sync_scratch`）、找空闲端口、等 HTTP 就绪（`wait_http`）、跑用例文件、停进程、按 0 通过 / 1 行为失败 / 2 环境错误映射退出码 |
| `scratch_build.py` | `examples/tools/scratch_build.py` | 代理需要构建或跑测试时，在 Git 可见文件的一次性副本里执行（`-- <命令>`，`--reuse NAME` 复用增量构建），避免 `target/`、`dist/` 等被忽略文件出现在工作区而判 `WORKSPACE_TAINTED` |
| `foundation-survey` 技能包 | `storyloop/skills/foundation-survey/` | 底座摸底的方法与八份文档模板；`storyloop skills` 装进目标仓库 |

## 三、什么时候不该自己写

1. 起子进程：用 `run_process`，不要 `subprocess.run`（会丢掉进程树回收、环境收窄和输出上限）。
2. 跑 Git：用 `_git` / `project_git`，不要拼 `git` 命令行（会触发仓库 hooks、继承全部环境变量）。
3. 写状态文件：用 `write_json` / `write_text_atomic`，不要 `open(..., "w")`（崩溃会留下半个文件）。
4. 接收路径：用 `safe_target` / `repository_relative`，不要 `Path(project) / user_input`（链接与 `..` 逃逸）。
5. 判断“代理改了什么”：用快照 + `audit_project_changes`，不要信代理返回的文件列表。
6. 跑测试或验收：用 `run_*_isolated`，不要在工作树里跑（测试可能改实现造成假绿）。
7. 报错：抛 `StoryLoopError` 并给出人能执行的原因；新停点在 `next_steps` 补下一步。
8. 校验代理返回：在 `role_schemas` 加 schema，不要在调用处手写键检查。
9. 显示或落盘外部输出：先 `redact`。
