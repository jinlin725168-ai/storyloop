# 快速上手：在自己的仓库跑通第一个需求

目标：从接入到第一个需求 `PASSED`，人只在做决定时介入。全程约 5 条命令，以 Python 项目和 Claude Code 为例；其他栈和 Codex 见文末。

前提：Python 3.11+、Git、一个**干净**（无未提交改动）、在具名分支上的仓库；`claude` 已登录（`claude -p "hi"` 能返回）。

```bash
python3 -m venv ~/.storyloop-venv && ~/.storyloop-venv/bin/pip install /path/to/storyloop
export PATH="$HOME/.storyloop-venv/bin:$PATH"
```

项目的测试由 StoryLoop 的解释器运行（命令里的 `{python}`），所以把项目依赖也装进这个 venv：`pip install -e ~/work/my-project[test]` 或按项目说明安装。

## 1. 接入：先看建议，再确认

```bash
storyloop init ~/work/my-project --detect --agent claude
```

这一步**不写任何文件**，只给出推断的工程画像：

| 字段 | 含义 | 推断依据 |
|---|---|---|
| `module_roots` | 功能变更可以改的目录 | 含 `__init__.py` 的顶层包，或 `src`/`lib`/`app` |
| `test_roots` | 测试目录 | `tests`、`test`、`spec`、`__tests__` |
| `unit_commands` | 单元测试命令 | 有 pytest 配置或 `conftest.py` 时用 pytest，否则 unittest |
| `scratch_paths` | 跑测试会产生的被忽略文件 | 按技术栈的常见缓存目录 |

`module_roots` 之外的已跟踪文件都成为**底座**，功能变更碰到就停下转人工。建议对了就确认：

```bash
storyloop init ~/work/my-project --detect --yes --agent claude
```

它会锁定底座、写好 Claude Code 适配器（`["{python}", "-m", "storyloop.claude_agent"]`）、跑一次准备度，并打印下一步。建议不对时，把输出的 `settings` 存成 JSON、改好后用 `storyloop init ~/work/my-project --settings settings.json --agent claude`。

准备度常见结果：

- `READY`：直接进第 2 步。
- `READINESS_APPROVAL_REQUIRED`：项目没有静态检查或系统冒烟层。输出给出确认命令（`approve --readiness <digest> --reason ...`）；接受这一缺口是你的决定。
- `PROJECT_NOT_READY`：测试命令跑不通或没有执行任何测试，多半是依赖没装进 StoryLoop 的 venv。修好后 `storyloop readiness ~/work/my-project`。

## 2. 一句话需求变成合同

```bash
storyloop change ~/work/my-project --idea "tables 命令支持按表名过滤"
```

产品角色读仓库后在终端逐条提问，回车选推荐项。草案在 `.storyloop/drafts/<变更 ID>/`：`proposal.md`（故事、规则、待决问题）和 `acceptance.json`（验收条件）。不满意就继续：

```bash
storyloop change ~/work/my-project --change <变更 ID> --message "过滤不区分大小写"
```

## 3. 批准，然后等

```bash
storyloop approve ~/work/my-project --change <变更 ID>
```

之后验收用例盲写并证明 RED、方案、特征化、实现、分层验证、独立审查、报告和本地 `--no-ff` 合并都自动进行。停下来时，输出会写明原因和要执行的命令。

## 4. 随时看全局

```bash
storyloop status ~/work/my-project
```

按"需要你决定 / 进行中 / 已完成"列出所有变更，每个待你决定的变更附下一步命令。单个变更的细节用 `status --change <ID>`，全过程用 `log --change <ID>`。

同时推进几个项目时，`storyloop inbox ~/work/a ~/work/b` 只列需要你决定的事项，最早的在前。想在需要你时收到提醒，在 `.storyloop/config.json` 加 `"notify_command": ["notify-send", "StoryLoop"]`（或任何读 stdin JSON 的脚本）：只在停到需要你决定或完成时运行，例行停点（引擎能自己修的阻断项、一次网络失败）不会打扰你。每个变更你敲过的命令按"决定 / 草案修订 / 续跑 / 修复"计数，见 `log` 和报告摘要的"人工触达"。

## 5. 看报告

合并后报告在 `reports/storyloop/<变更 ID>/`：`report.md` 是一页摘要（结论、验收追溯、各层结果、判据自证、审查、成本），`report.json` 是完整证据。分节的完整报告（命令、过程、时间线）按需生成，不入库：

```bash
storyloop report ~/work/my-project --change <变更 ID> --full
```

## 其他栈与代理

- `--agent codex` 用 Codex CLI；`--agent auto` 按 PATH 上能找到的选（先 Claude Code）。
- Maven、Gradle、Node 项目同样可用 `--detect`；推断不出 `unit_commands` 或 `module_roots` 时会要求改用 `--settings`。混合栈或要人工补全钩子与规范时用 `init --scaffold`，见[接入手册](onboarding-a-repository.md)。
- 完整命令见 [README](../README.md#命令)，概念见[概念手册](concepts.md)，逐步讲解见[入门教程](tutorial.md)。
