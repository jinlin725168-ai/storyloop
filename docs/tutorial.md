# 入门教程：30 分钟跑完一次闭环

用一个最小的 Python 待办清单仓库，从初始化到 no-ff 合并走一遍。所有输出都来自一次真实运行（2026-09-23，Claude Code 适配器，总费用 1.14 USD，代理累计 3 分半）。读完你会知道每个命令做了什么、仓库里长出了什么、哪里需要人。

前置：Linux/WSL2 或 Windows，Python 3.11+，Git，已登录的 Claude Code 或 Codex CLI。

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

`storyloop-settings.json` 是接入的核心，三行就够：

```json
{
  "unit_commands": [["{python}", "-m", "unittest", "discover", "-s", "tests"]],
  "module_roots": ["app"],
  "test_roots": ["tests"],
  "scratch_paths": ["__pycache__"]
}
```

`module_roots` 是功能变更可以改的目录，`test_roots` 是测试文件的位置（implement 和 characterize 都可以写，也是特征化门判断"只改了测试"的依据）；`scratch_paths` 声明代理跑测试时允许产生的被忽略文件。

## 1. 锁定底座

```bash
$SL init /tmp/todo-demo --settings /tmp/todo-demo/storyloop-settings.json
```

```text
BASELINE_LOCKED
project: /tmp/todo-demo
```

发生了什么：`.storyloop/` 出现（写进 `.git/info/exclude`，不进 Git），`baseline.json` 记下每个已跟踪文件的 SHA-256 和底座版本 `1.0.0`。可变区之外的文件（`README.md`、`.gitignore`、`storyloop-settings.json`）现在是底座，功能变更改它们会停下来。

默认适配器是 Codex。用 Claude Code 时改 `.storyloop/config.json`：

```json
{
  "adapter": "command",
  "agent_command": ["{python}", "/home/me/work/storyloop/examples/claude-code/claude_agent.py"],
  "max_attempts": 3
}
```

## 2. 一句话变成合同

```bash
$SL change /tmp/todo-demo --idea "待办可以标记完成；列出时显示每条的状态；已完成的不能再标记一次"
```

产品角色读仓库、写草案到 `.storyloop/drafts/FEAT-20260923-001/`，然后在终端里逐条提问。这次它提了三个问题，每个都有推荐项和"为什么重要"：

```text
Q1 列出待办时，每条的状态用什么形式显示？
   1) 行首显示 [x] / [ ]，例如 "1 [x] 买牛奶"（推荐）
   2) 行尾显示中文"已完成"/"未完成"
   3) 只有已完成的在行首加 ✓
   为什么重要：命令行待办常见写法，一眼看出状态，也方便测试按固定格式断言。
Q2 对已完成的待办再标记一次时，命令应以什么结果结束？
   1) 输出错误提示，以非零退出码结束，存储不变（推荐）
   2) 输出提示，退出码 0，存储不变
Q3 要标记的编号不存在时，命令应以什么结果结束？
   1) 输出错误提示，以非零退出码结束，存储不变（推荐）
   2) 什么都不做，退出码 0
```

回车选推荐项，输入序号或自由文本选别的，`q` 结束本轮。每个回答立即触发一次修订。没有终端时（CI、后台）加 `--no-interactive`，问题保持开放，用一条消息回答：

```bash
$SL change /tmp/todo-demo --change FEAT-20260923-001 \
  --message "Q1 选推荐项；Q2 选推荐项；Q3 选推荐项"
```

看一眼草案。`proposal.md` 的关键部分：

```text
能力：todo-completion

## 用户故事
- S1 [P1] 标记待办为已完成（独立验证：用一个临时存储文件添加一条待办，按它的编号执行标记完成，
  再读取存储，确认该条状态为已完成；对同一条再标记一次，确认被拒绝且存储不变。）
- S2 [P2] 列出待办时显示状态（独立验证：……）

## 澄清记录
- 列出待办时，每条的状态用什么形式显示？ → 行首显示 [x] 表示已完成，[ ] 表示未完成
- 对已完成的待办再标记一次时…… → 输出错误提示，以非零退出码结束，存储不变
- 要标记的编号不存在时…… → 输出错误提示，以非零退出码结束，存储不变

## 待决问题
- 无
```

`acceptance.json` 有 12 条 Given/When/Then 验收条件，全部 `automated`，例如：

```text
A4  GIVEN 存储中有编号为 1 的待办，并且它已经是已完成
    WHEN  用户再次把编号 1 标记为已完成
    THEN  这次标记被拒绝，存储内容与标记前完全一致
A9  GIVEN 存储中编号 1 的待办"买牛奶"已完成
    WHEN  用户列出待办
    THEN  输出中有一行为"1 [x] 买牛奶"
```

`checklist.md` 是需求质量门的结果，这次 18 项全过、0 阻断。有阻断项（未回答的问题、没有验收条件的故事）时 `approve` 会拒绝。

## 3. 批准，然后等

```bash
$SL approve /tmp/todo-demo --change FEAT-20260923-001
```

这是本教程唯一需要人的地方（合同里没有 `manual` 条件）。接下来引擎自动做的事，按时间顺序：

1. 锁定合同指纹，建分支 `feature/FEAT-20260923-001`，把合同物化到 `openspec/changes/FEAT-20260923-001/`（含增量规格 `specs/todo-completion/spec.md`）。
2. acceptance 角色**只看合同**盲写 `acceptance/changes/FEAT-20260923-001/`：`_todo.py`（调用 CLI 的辅助）、`test_mark.py`、`test_list.py` 和 `manifest.json`。manifest 把 4 个测试映射到 12 条验收条件：

   ```json
   {"id": "s1-reject-completed", "criteria": ["A4", "A5"],
    "command": ["{python}", "acceptance/changes/FEAT-20260923-001/test_mark.py", "RejectCompletedTest"],
    "timeout_seconds": 120}
   ```

3. 合同和套件一起提交：`test(FEAT-20260923-001): lock approved contract and smoke acceptance`。
4. 在隔离候选副本里跑一次冒烟，证明 RED。证据在 `.storyloop/evidence/FEAT-20260923-001/smoke-red.json`，4 个测试都是 `FAIL` 退出码 1，原因正是功能缺失：

   ```text
   AssertionError: 无法从 CLI 帮助中找到唯一的标记完成子命令（候选: []）。--help 输出:
   usage: todo [-h] --store STORE {add,list} ...
   ```

   注意盲写的用例不知道子命令会叫什么，所以它从 `--help` 里找。这是 acceptance 角色自己的策略，合同只说了行为。
5. 实现代理拿到合同，改了 `app/store.py`、`app/cli.py`，加了 `tests/test_completion.py`，返回 summary 和一份 design。
6. 引擎跑单元回归（`unittest discover`，9 个测试）、变更验收（12 个测试全绿）、边界审计（只动了可变区的 3 个文件）。这次运行时引擎还没有特征化门、系统冒烟和集成/静态层，它们的位置见使用指南 5c。
7. 审查角色独立读代码和证据，给出 `pass`。
8. 提交实现，写 `design.md`、`decisions.md`，把增量并入活规格 `openspec/specs/todo-completion/spec.md`，生成报告，提交文档，切回 `main` 做 no-ff 合并，写回执。

终端最后打印：

```text
PASSED
change_id: FEAT-20260923-001
report: /tmp/todo-demo/reports/storyloop/FEAT-20260923-001/report.json
```

这次一轮就过了。如果冒烟或审查失败，引擎会带着失败证据再调用实现代理，最多 `max_attempts` 轮。

## 4. 看结果

Git 历史：

```text
* a9db95a docs(FEAT-20260923-001): record verified merge
*   c85d842 Merge branch 'feature/FEAT-20260923-001'
|\
| * 2e6d5b9 docs(FEAT-20260923-001): add acceptance evidence report
| * 03c1631 feat(FEAT-20260923-001): implement approved product change
| * f9c50b7 test(FEAT-20260923-001): lock approved contract and smoke acceptance
|/
* 9254111 初始版本：待办清单命令行
```

三个提交的分工是固定的：合同与套件、实现、文档与报告。合并是 no-ff，回执提交把合并 SHA 写回报告。

仓库里新长出的文件：

```text
acceptance/changes/FEAT-20260923-001/   _todo.py manifest.json test_list.py test_mark.py
openspec/changes/FEAT-20260923-001/   proposal.md stories.json acceptance.json traceability.json
                                      checklist.md approval.json specs/todo-completion/spec.md
                                      design.md decisions.md
openspec/specs/todo-completion/spec.md
reports/storyloop/FEAT-20260923-001/  report.json report.md
```

`report.md` 的追溯表，每条验收条件对应测试、结果和证据摘要：

```text
| S1 标记待办为已完成 | A4 这次标记被拒绝，存储内容与标记前完全一致 | s1-reject-completed | PASS | a6ce1c49… |
| S2 列出待办时显示状态 | A9 输出中有一行为"1 [x] 买牛奶"           | s2-list-status      | PASS | a6ce1c49… |
```

`design.md` 是实现代理写的方案，例如它为什么选了 `done` 布尔字段而不是 `status` 文本、为什么在写入前检查以保证拒绝时文件字节不变。`decisions.md` 是引擎汇总的人为决策：三条澄清回答、修订消息，以及（这次没有的）代理提问回答、人工验收备注、复位和 acceptance-repair 记录。

成本一节：

```text
- 代理调用：5 次（失败 0 次），代理累计 3 分 33 秒
- 费用：1.1420 USD
| product | 2 | 10 | 0 分 59 秒 | 0.3021 |
| acceptance | 1 |  9 | 1 分 21 秒 | 0.3336 |
| implement | 1 | 18 | 0 分 54 秒 | 0.3458 |
| review  | 1 |  7 | 0 分 18 秒 | 0.1605 |
```

状态查询：

```bash
$SL status /tmp/todo-demo --change FEAT-20260923-001 --json
```

```json
{"status": "PASSED", "revision": 2,
 "run": {"status": "PASSED", "attempt": 1, "implementation_commit": "03c16316…", "merge_commit": "c85d842e…"},
 "telemetry": {"calls": 5, "cost_usd": 1.142, "agent_ms": 213264}}
```

## 5. 在 CI 里重跑冒烟

```bash
$SL ci /tmp/todo-demo --change FEAT-20260923-001
```

```text
PASSED
change_id: FEAT-20260923-001
```

它从 Git 可见文件构造候选副本重跑受保护的套件；套件字节、清单、证据都有指纹。CI 平台只需加这一条命令。

## 6. 接下来试试这些

- **触发一次停点**：把 `tests` 从 `module_roots` 拿掉重新 init（没有变更时 `rm -rf .storyloop`），再跑一个会加测试的变更，看 `BASE_CHANGE_REQUIRED` 和 `base-propose` / `base-update`。
- **人工验收**：在意图里写一条机器判断不了的要求（"错误提示要让人看得懂"），产品角色会把它标成 `manual`，运行会停在 `MANUAL_ACCEPTANCE_REQUIRED`，用 `accept --criterion A13 --note "…"` 再 `run`。
- **acceptance-repair**：故意把套件里的一个断言写错再批准，观察实现过不了后用 `acceptance-repair --reason "…"` 修套件并重证 RED。
- **系统冒烟**：`smoke-propose --change FEAT-20260923-001` 看 acceptance 角色提议把哪条核心链路并入 `acceptance/smoke/`，审核后 `smoke-apply`。
- **特征化门**：在 settings 里加 `"test_roots": ["tests"]`，再跑一个会修改 `app/store.py` 的变更，看 characterize 角色先写锁定现有行为的测试并跑绿。
- **第二个变更**：再来一句"待办可以撤销完成"，看它如何修改既有需求并在活规格里做取代记录。

然后读 [使用指南](product-iteration-guide.md) 了解每个停点，按 [接入手册](onboarding-a-repository.md) 把自己的仓库接进来。
