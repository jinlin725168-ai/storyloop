# 入门教程：30 分钟跑完一次闭环

用一个最小的 Python 待办清单仓库，从初始化到 no-ff 合并走一遍。所有输出都来自一次真实运行（2026-09-29，Claude Code 适配器，总费用 2.91 USD，代理累计 6 分 22 秒，闭环墙钟 8 分钟）。读完你会知道每个命令做了什么、仓库里长出了什么、哪里需要人。这次运行还真实撞上了一次停点（实现代理改了底座里的 README），教程照实记录了怎么处理。

2026-10-14 用当前版本从干净仓库照本教程重走了一遍（Claude Code 适配器，8 次调用，4.79 USD）：同样停在 README 的底座停点，按 3a 处理后第 2 轮 PASSED，Git 历史与第 4 节一致。代理每次提的问题、写的测试文件名和条数会不同，下文的输出以 2026-09-29 那次为准，与当前版本不同的地方单独注明。

前置：Linux/WSL2 或 Windows，Python 3.11+，Git（已配置提交身份：`git config --global user.name/user.email`，或在仓库里单独配置；闭环用这个身份提交），已登录的 Claude Code 或 Codex CLI。

## 0. 安装引擎并准备示例仓库

```bash
git clone <storyloop 仓库> ~/work/storyloop
python3 -m venv ~/work/storyloop/.venv
~/work/storyloop/.venv/bin/pip install ~/work/storyloop
export SL=~/work/storyloop/.venv/bin/storyloop

cp -r ~/work/storyloop/examples/todo-demo /tmp/todo-demo
git -C /tmp/todo-demo init -q -b main
git -C /tmp/todo-demo add -A
git -C /tmp/todo-demo commit -qm "初始版本：待办清单命令行"
```

示例仓库只有这些文件：

```text
app/cli.py                  python -m app.cli --store FILE add TITLE | list
app/store.py                JSON 文件存储：load / save / add
tests/test_store.py         一个回归测试
storyloop-settings.json     告诉引擎什么能改、怎么回归
README.md  .gitignore
```

`storyloop-settings.json` 是接入的核心，四行就够：

```json
{
  "unit_commands": [["{python}", "-m", "unittest", "discover", "-s", "tests"]],
  "module_roots": ["app"],
  "test_roots": ["tests"],
  "scratch_paths": ["__pycache__"]
}
```

`module_roots` 是功能变更可以改的目录；`test_roots` 是测试文件的位置，implement 和 characterize 都可以写，也是特征化门判断"只改了测试"的依据；`scratch_paths` 声明代理跑测试时允许产生的被忽略文件。这个仓库没有集成测试、系统冒烟和静态检查，报告里对应的层会显示 SKIPPED，而不是失败。

## 1. 锁定底座

```bash
$SL init /tmp/todo-demo --settings /tmp/todo-demo/storyloop-settings.json
```

```text
BASELINE_LOCKED
project: /tmp/todo-demo
```

发生了什么：`.storyloop/` 出现（写进 `.git/info/exclude`，不进 Git），`baseline.json` 记下每个已跟踪文件的 SHA-256 和底座版本 `1.0.0`。可变区之外的文件（`README.md`、`.gitignore`、`storyloop-settings.json`）现在是底座，功能变更改它们会停下来。第 3 节会真的撞上这条规则。

默认适配器是 Codex。用 Claude Code 时改 `.storyloop/config.json` 里的 `adapter` 和 `agent_command` 两项，其余字段（包括 init 写入的 `profile`）保持不动，不要用下面的片段整体覆盖文件（`design_min_stories` 是 init 写好的默认值，两个故事以上才出方案）：

```json
{
  "adapter": "command",
  "agent_command": ["{python}", "/home/me/work/storyloop/examples/claude-code/claude_agent.py"],
  "max_attempts": 3,
  "design_min_stories": 2
}
```

init 写好的其他默认值：`agent_timeout_seconds` 14400（挂死保护）、`check_timeout_seconds` 300（单条测试命令上限）。完整配置表见 [api.md](api.md#三配置)。

## 2. 一句话变成合同

```bash
$SL change /tmp/todo-demo --idea "待办可以标记完成；列出时显示每条的状态；已完成的不能再标记一次"
```

产品角色读仓库、写草案到 `.storyloop/drafts/FEAT-20260929-001/`，然后在终端里逐条提问。这次它提了三个问题，每个都有推荐项和"为什么重要"：

```text
Q1 列出待办时，每条的状态用什么形式显示？
   1) 行首复选框：未完成为"[ ] 1 买牛奶"，已完成为"[x] 1 买牛奶"（推荐）
   2) 行尾中文文字："1 买牛奶 未完成" / "1 买牛奶 已完成"
   3) 只给已完成的加标记，未完成的保持原来的"1 买牛奶"格式
   为什么重要：列表输出格式决定自动化验收怎么断言，也会影响依赖现有"编号 标题"格式的使用者。
Q2 对已完成的待办再标记一次时，命令应如何反馈？
   1) 输出错误提示并以非零退出码结束，数据不变（推荐）
   2) 输出提示但以退出码 0 结束，数据不变（视为幂等）
Q3 标记一个不存在的编号时，命令应如何反馈？
   1) 输出错误提示并以非零退出码结束，数据不变（推荐）
   2) 静默忽略，以退出码 0 结束
```

回车选推荐项，输入序号或自由文本选别的，`q` 结束本轮。每个回答立即触发一次修订。没有终端时（CI、后台）加 `--no-interactive`，问题保持开放：输出的 `next:` 会写明有几个待决问题、几个阻断项，问题全文在草案 `proposal.md` 的“待决问题”一节（`--json` 输出里是 `product.questions`）。用一条消息回答：

```bash
$SL change /tmp/todo-demo --change FEAT-20260929-001 \
  --message "Q1 选推荐项；Q2 选推荐项；Q3 选推荐项" --no-interactive
```

看一眼草案。`proposal.md` 的关键部分：

```text
能力：todo-completion

## 用户故事
- S1 [P1] 标记待办完成并在列表中显示状态（独立验证：用一个临时存储文件添加两条待办，将其中一条标记为完成，
  然后列出待办，确认已完成的那条显示为"[x] 编号 标题"、另一条显示为"[ ] 编号 标题"。）
- S2 [P2] 拒绝重复标记已完成的待办（独立验证：……再标记一次，确认第二次输出错误提示、
  以非零退出码结束且存储内容不变。）

## 澄清记录
- 列出待办时，每条的状态用什么形式显示？ → 行首复选框：未完成为"[ ] 1 买牛奶"，已完成为"[x] 1 买牛奶"
- 对已完成的待办再标记一次时，命令应如何反馈？ → 输出错误提示并以非零退出码结束，数据不变
- 标记一个不存在的编号时，命令应如何反馈？ → 输出错误提示并以非零退出码结束，数据不变

## 待决问题
- 无
```

`acceptance.json` 有 11 条 Given/When/Then 验收条件，全部 `automated`，例如：

```text
A6  GIVEN 存储文件是在本功能上线前写入的、不带状态信息的待办"1 买牛奶"
    WHEN  用户列出待办
    THEN  这条待办显示为"[ ] 1 买牛奶"
A9  GIVEN 存储中有一条已完成的待办"1 买牛奶"
    WHEN  用户再次将编号 1 标记为完成
    THEN  存储内容与操作前完全相同
```

A6 值得注意：意图里没提旧数据，是产品角色读了 `app/store.py` 后自己补上的兼容要求。`checklist.md` 是需求质量门的结果，这次 18 项全过、0 阻断。有阻断项（未回答的问题、没有验收条件的故事）时 `approve` 会拒绝。

当前版本的合同还会按行类型组织验收条件（新行为、回归、状态保持、非目标等，见[概念手册](concepts.md)），检查表项数随之增加；2026-10-14 那次是 5 个问题、13 条验收条件、32 项全过。

## 3. 批准，然后等

```bash
$SL approve /tmp/todo-demo --change FEAT-20260929-001
```

批准即开跑。接下来引擎自动做的事，按时间顺序：

1. 锁定合同指纹，建分支 `feature/FEAT-20260929-001`，把合同物化到 `openspec/changes/FEAT-20260929-001/`（含增量规格 `specs/todo-completion/spec.md`）。
2. acceptance 角色**只看合同**盲写 `acceptance/changes/FEAT-20260929-001/`：`test_todo_completion.py` 和 `manifest.json`。manifest 把 4 个测试映射到 11 条验收条件：

   ```json
   {"id": "s2-repeat-mark", "criteria": ["A8", "A9", "A10"],
    "command": ["{python}", "acceptance/changes/FEAT-20260929-001/test_todo_completion.py", "RepeatMarkTest"],
    "timeout_seconds": 120}
   ```

3. 合同和套件一起提交：`test(FEAT-20260929-001): lock approved contract and smoke acceptance`。
4. 在隔离候选副本里跑一次验收，证明 RED。证据在 `.storyloop/evidence/FEAT-20260929-001/acceptance-red.json`，4 个测试都是 `FAIL`，原因正是功能缺失：

   ```text
   AssertionError: no subcommand marks a todo as done (tried: done, complete, finish, check, mark-done, mark)
   ```

   盲写的用例不知道子命令会叫什么，所以它列了候选名逐个试。这是 acceptance 角色自己的策略，合同只说了行为。
5. **design 角色**出方案（两个故事，达到 `design_min_stories`）。它只读不写，返回要动的文件 `README.md`、`app/cli.py`、`app/store.py` 和一份方案：五条决策（子命令叫 `done`；状态存布尔 `done` 字段，缺省视为未完成；业务规则放 store 用异常上报；错误走 stderr 退出码 1；格式化留在 cli），接口表和风险。方案在 `.storyloop/state/runs/` 里等后面的角色取用，完成后会成为 `design.md` 的前半部分。
6. **characterize 角色**给方案里要动的三个文件先写锁定现有行为的测试：`tests/test_characterization_store.py`（load 不建文件、save 的 JSON 是 UTF-8 缩进两格……）、`test_characterization_cli.py`、`test_characterization_readme.py`。引擎在旧代码上跑绿后提交：`test(FEAT-20260929-001): characterize touched paths`，187 行。
7. implement 角色拿到合同、方案和特征化结果，先写 `tests/test_completion.py` 看着它们红（`design.md` 的 Tests First 一节逐条记了红的原因，例如 `AttributeError: module 'app.store' has no attribute 'mark_done'`），再改 `app/store.py`、`app/cli.py`。它还按方案给 README 加了一行用法。
8. 边界审计发现 `README.md` 不在可变区，运行停下：

   ```text
   BASE_CHANGE_REQUIRED
   change_id: FEAT-20260929-001
   ```

   当前版本的输出还会多一行 `next:`，给出可以直接复制的下一步：在源分支单独提交提案里的路径、`base-update`、把源分支合进功能分支、再 `run`。

这是本教程第一个需要人的地方。任何停点都可以先跑 `$SL log /tmp/todo-demo --change FEAT-20260929-001`：它列出每次代理调用的结果、耗时、费用、会话 ID 与调用记录目录，每轮失败卡在哪一层，最后一行是下一步命令。

### 3a. 处理停点：把底座改动独立锁进去

`status --json` 里 `run.violations` 是 `[{"path": "README.md", "category": "FOUNDATION_CHANGE"}]`，同时 `.storyloop/base-proposals/BASE-20260929-001.json` 已经生成，只列了这一条路径。引擎不会替你决定 README 该不该改，也不会自动把底座路径放宽成业务路径。这次的判断是：README 里的用法确实该加，那就走底座迭代。

```bash
cd /tmp/todo-demo
git diff README.md > /tmp/readme.patch      # 留下代理写的那一行
git checkout -- . && git clean -fd           # 丢掉这一轮实现，下一轮会重做
git checkout main
git apply /tmp/readme.patch
git commit -am "docs: README 补充 done 子命令用法"
$SL base-update /tmp/todo-demo --proposal BASE-20260929-001
```

```text
BASELINE_UPGRADED
```

`base-update` 要求提交的路径与提案完全一致、工作树干净、至少一条回归命令真实通过，然后把底座版本升到 `1.0.1`。回到功能分支，把新的 main 合进来，再续跑：

```bash
git checkout feature/FEAT-20260929-001
git merge --no-edit main
$SL run /tmp/todo-demo --change FEAT-20260929-001
```

续跑时引擎发现底座头已经前移，且功能分支包含它，就把运行的源头改到新底座，并记入 `run.base_upgrades`（如果 main 是因为别的原因动了，完成阶段仍会拒绝合并）。第 2 轮实现从合同和特征化提交重新开始：

9. implement 第 2 轮：同样先红后绿，这次没碰 README（它已经有那一行了），提交 `feat(FEAT-20260929-001): implement approved product change`，动了 3 个文件 168 行。
10. 分层验证：单元回归 33 个测试（含 3 个特征化文件）、变更验收 4 个用例 11 条全绿、边界审计只动了可变区和 `tests/`。
11. **review 角色**用四个并行视角（正确性、安全、架构、测试）各自独立读代码和证据，主评审合并，给出 `pass`。合并意见里明确记下了两处偏离方案及理由：测试放在新文件而不是 `test_store.py`，README 没改因为已经有了。
12. 写 `design.md`（方案 + Implementation record）、`decisions.md`，把增量并入活规格 `openspec/specs/todo-completion/spec.md`，生成报告，提交文档，切回 `main` 做 no-ff 合并，写回执。

终端最后打印：

```text
PASSED
change_id: FEAT-20260929-001
report: /tmp/todo-demo/reports/storyloop/FEAT-20260929-001/report.json
```

如果验收或审查失败，引擎会带着失败证据再调用实现代理，最多 `max_attempts` 轮。

## 4. 看结果

Git 历史：

```text
* a820825 docs(FEAT-20260929-001): record verified merge
*   537fbbf Merge branch 'feature/FEAT-20260929-001'
|\
| * 8ad2461 docs(FEAT-20260929-001): add acceptance evidence report
| * f4af01a feat(FEAT-20260929-001): implement approved product change
| *   233a472 Merge branch 'main' into feature/FEAT-20260929-001
| |\
| |/
|/|
* | dbaeb74 docs: README 补充 done 子命令用法
| * 44e9002 test(FEAT-20260929-001): characterize touched paths
| * 9cf6248 test(FEAT-20260929-001): lock approved contract and smoke acceptance
|/
* 70f5bc6 初始版本：待办清单命令行
```

功能分支上四个提交的分工是固定的：合同与套件、特征化测试、实现、文档与报告。底座改动是 main 上独立的一个提交。合并是 no-ff，回执提交把合并 SHA 写回报告。

仓库里新长出的文件：

```text
acceptance/changes/FEAT-20260929-001/   manifest.json test_todo_completion.py
openspec/changes/FEAT-20260929-001/     proposal.md stories.json acceptance.json traceability.json
                                        checklist.md approval.json specs/todo-completion/spec.md
                                        design.md decisions.md
openspec/specs/todo-completion/spec.md
reports/storyloop/FEAT-20260929-001/    report.json report.md
tests/test_characterization_{store,cli,readme}.py  tests/test_completion.py
```

`report.md` 的追溯表，每条验收条件对应测试、结果和证据摘要：

```text
| S1 标记待办完成并在列表中显示状态 | A6 这条待办显示为"[ ] 1 买牛奶"       | s1-list-status | PASS | 9dcd58f8… |
| S2 拒绝重复标记已完成的待办       | A9 存储内容与操作前完全相同           | s2-repeat-mark | PASS | 9dcd58f8… |
```

当前版本的报告在分层验证之后还有一节“判据自证”：每条测试命令实际执行了多少个用例、旧代码上的基线是多少。执行数为 0 或低于基线，这一层判失败，防止“一个用例都没跑却显示通过”。2026-10-14 那次单元层执行 29 个、基线 19 个。

分层验证一节把五层都列出来，没配置的层是 SKIPPED 并给出原因：

```text
| unit（单元测试）        | PASSED  | 1/1 |                        |
| integration（集成测试） | SKIPPED | 0/0 | NO_REGRESSION_COMMANDS |
| acceptance（变更验收）  | PASSED  | 4/4 |                        |
| smoke（系统冒烟）       | SKIPPED | 0/0 | NO_SYSTEM_SMOKE_SUITE  |
| checks（静态检查）      | SKIPPED | 0/0 | NO_REGRESSION_COMMANDS |
```

`design.md` 前半是 design 角色的方案（Context / Goals / Decisions D1–D5 / Files / Data And Interfaces / Risks），后半 `## Implementation record` 是实现代理的记录，其中 Tests First 一节列出 11 个先红的测试和各自红的原因。`decisions.md` 是引擎汇总的人为决策：意图原文、一次修订消息、三条澄清回答，以及（这次没有的）代理提问回答、人工验收备注、复位和 acceptance-repair 记录。

成本一节：

```text
- 代理调用：8 次（失败 0 次），代理累计 6 分 22 秒
- 闭环墙钟：8 分 0 秒，实现尝试 2 轮
- 费用：2.9100 USD
| acceptance   | 1 |  8 | 0 分 58 秒 | 0.2393 |
| characterize | 1 | 11 | 0 分 43 秒 | 0.2656 |
| design       | 1 |  8 | 0 分 50 秒 | 0.2395 |
| implement    | 2 | 36 | 2 分 20 秒 | 0.8928 |
| product      | 2 |  8 | 0 分 54 秒 | 0.2681 |
| review       | 1 | 24 | 0 分 34 秒 | 1.0047 |
```

review 一次调用花了三分之一的钱：四个视角子代理加主评审共 5 个会话，账本里记为 `perspectives: 4`。implement 是两轮各一次调用。

状态查询：

```bash
$SL status /tmp/todo-demo --change FEAT-20260929-001 --json
```

```json
{"status": "PASSED", "revision": 2,
 "run": {"status": "PASSED", "attempt": 2,
         "implementation_commit": "f4af01a8…", "merge_commit": "537fbbf6…",
         "base_upgrades": [{"baseline_version": "1.0.1", "from": "70f5bc64…", "to": "dbaeb74e…"}]},
 "telemetry": {"calls": 8, "cost_usd": 2.91, "agent_ms": 382390}}
```

（节选。当前版本的返回还带 `next` 字段，`PASSED` 时提示 `storyloop report` 与可选的 `smoke-propose`。）

## 5. 在 CI 里重跑验收

```bash
$SL ci /tmp/todo-demo --change FEAT-20260929-001
```

```text
PASSED
change_id: FEAT-20260929-001
```

它从 Git 可见文件构造候选副本重跑受保护的套件；套件字节、清单、证据都有指纹。CI 平台只需加这一条命令。

## 6. 接下来试试这些

- **避免这次的停点**：如果你希望代理能改 README，在没有进行中变更时把它列进 settings 的 `module_roots`，删掉 `.storyloop` 重新 init；或者接受教程里的做法，让文档改动作为底座迭代单独审。
- **小改动不出方案**：再来一句只有一个故事的意图（"list 输出按编号倒序"），看 `design_plan.status` 变成 `SKIPPED`，原因是 `small change: 1 story < design_min_stories 2`。
- **人工验收**：在意图里写一条机器判断不了的要求（"错误提示要让人看得懂"），产品角色会把它标成 `manual`，运行会停在 `MANUAL_ACCEPTANCE_REQUIRED`，用 `accept --criterion A12 --note "…"` 再 `run`。
- **acceptance-repair**：套件是批准后由 acceptance 角色生成的受保护合同，人不能直接改。如果实现一直过不了、而你判断是用例本身有缺陷（定位器、时序、数据准备），用 `acceptance-repair --reason "…"` 让 acceptance 角色修自己的套件并从合同提交重证 RED。
- **系统冒烟**：`smoke-propose --change FEAT-20260929-001` 看 acceptance 角色提议把哪条核心链路并入 `acceptance/smoke/`，审核后 `smoke-apply`，下一个变更的报告里 smoke 层就不再是 SKIPPED。
- **第二个变更**：再来一句"待办可以撤销完成"，看它如何修改既有需求并在活规格里做取代记录。

然后读 [使用指南](product-iteration-guide.md) 了解每个停点，按 [接入手册](onboarding-a-repository.md) 把自己的仓库接进来。
