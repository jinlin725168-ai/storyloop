# 接入规范草案

未初始化、需人工补全。先审阅并提交，再使用原 init 锁定；初始化不代表 readiness READY。

- 填写 module_roots、test_roots 和 integration_paths，明确哪些模块可写。
- acceptance/tools、此规范、settings 和 onboarding.json 必须留在底座保护范围。
- 单元、集成、验收、系统冒烟、静态检查分别选择真实执行命令；不得用空通过脚本代替。
- 冒烟只接收 manifest 指定的 case，不自动发现目录，也不附加 acceptance/ui。
- source_files 只能填写明确安全文件；不扫描目录，不复制 .env、密钥或凭证。
- 依赖需预热；依赖缓存显式声明，构建输出使用每次独占 scratch，不下载或安装。
- 明确 build/start/ready/run_case/cleanup；不用的阶段需 not_applicable 及理由。
- 请人工确认 settings 中的 `base_build_commands`：空数组明确表示项目没有构建步骤；有构建步骤时填写 argv 命令。
- 数据隔离、业务断言、只读健康探针和必要的权限须由项目确定。本模板不配置数据库或登录。
- 运行 0 表示实际执行全绿，1 仅表示行为断言失败，2 表示环境/配置/清理错误或零执行。
- 完成后将 onboarding.json 的 reviewed 标记为 true 并提交；后续修改保护文件走 BASE。

## 项目约定（人工填写）

模块所有权、测试目录、集成入口、命令与缓存寻址、只读健康条件、仍有的 readiness 缺口。

### 本仓库（StoryLoop 自身）

- 可写模块：`storyloop/`（引擎）、`docs/`、`examples/`；测试在 `tests/`，按 `tests/suites.py` 归属登记新测试模块。
- 底座：`pyproject.toml`、`acceptance/**`、`.github/**`、`storyloop_cli.py` 及根目录其它文件。
- 单元层运行 `python tests/run.py --suite core`；静态检查运行 `python -m ruff check storyloop tests examples`（需在引擎解释器环境中安装 ruff==0.8.4）；无集成命令。
- 约定：中文用户可见文案；`tests/test_architecture.py` 保持零豁免，新模块须在 `storyloop/architecture.toml` 与 `docs/modules.md` 登记；函数不超过 50 行、文件不超过 500 行。
- 系统冒烟只校验 CLI 版本与帮助可用；业务断言写在变更自己的验收用例中。

- 被测业务就是 StoryLoop 自身：验收用例不得在测试进程内 import `storyloop` 或 `tests`；须通过候选镜像中的子进程加载被测代码，避免误用已安装的引擎包。
- 自举验收若需要 `storyloop.testing` helper，manifest 命令须以 `{storyloop_harness}` 开头，由当前引擎安装包的隔离 launcher 执行。候选侧的测试命令或脚本不得导入 `storyloop.testing.*`；RED 预检命中 `HARNESS_SHADOWED` 时由验收角色按文件证据自动修复，实施阶段验收命中则停给人。业务 CLI/代码仍在候选子进程中运行。
- 构造变更与运行状态夹具时，优先用公开 CLI（`storyloop init`、`storyloop change` 等）或公开函数生成，不要手写状态 JSON。确需手写变更文件时，`product` 的 `version`、`goal`、`non_goals`、`questions`、`stories` 必须完整；下面是可由 `load_change` 读取的最小示例，实际验收应按合同填写故事与判据：

```json storyloop-change-fixture
{
  "version": 1,
  "change_id": "FEAT-fixture",
  "status": "DRAFT",
  "revision": 1,
  "idea": "核对现有行为",
  "product": {
    "version": 1,
    "goal": "核对现有行为",
    "non_goals": [],
    "questions": [],
    "stories": [{
      "id": "ST-1", "title": "核对行为", "as_a": "使用者",
      "want": "执行现有流程", "so_that": "确认结果",
      "rules": [], "out_of_scope": [], "depends_on": [],
      "acceptance": [{
        "id": "AC-1", "given": "已有夹具", "when": "执行流程",
        "then": "返回预期结果", "kind": "automated"
      }]
    }]
  },
  "created_at": "2026-11-05T00:00:00Z",
  "updated_at": "2026-11-05T00:00:00Z",
  "history": [],
  "approval": null
}
```

- 调用 `bootstrap_project` 前先在设置中声明 `module_roots` 和 `test_roots`。含 `:`、`[ab]` 等特殊字符的测试路径须在 bootstrap 之后创建；baseline 路径安全检查会拒绝已跟踪的 `:` 路径。
- 每个 RED 用例先核对夹具前提，再断言被测行为；夹具错误不能当成行为失败来证明 RED。自举 Case 的 `precondition` 等 helper 只在 `{storyloop_harness}` 启动的受保护 executor 中使用，候选侧普通测试进程不导入 `storyloop.testing.*`；被测 StoryLoop 代码仍由候选子进程加载。

最小自举验收示例（假定 onboarding 已配置受保护 executor `acceptance/tools/python_smoke.py`；把 `<change_id>` 换成实际变更 ID）：

```json
{"version":1,"tests":[{"id":"SMOKE-1","criteria":["AC-1"],"command":["{storyloop_harness}","acceptance/tools/python_smoke.py","acceptance/changes/<change_id>/test_cli.py"],"timeout_seconds":30}]}
```

```python
# acceptance/changes/<change_id>/test_cli.py：此 case 由受保护 executor 加载；业务仍在子进程运行。
import subprocess
import sys
import unittest
from pathlib import Path

class Case(unittest.TestCase):
    def test_cli(self):
        result = subprocess.run([sys.executable, "-m", "storyloop", "--version"],
                                cwd=Path.cwd(), capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0)
```

需要 `precondition` 时，在受保护 executor 中从引擎包导入并检查前提；不要把 helper 导入写进由 `{python}` 直接执行的候选侧脚本。
