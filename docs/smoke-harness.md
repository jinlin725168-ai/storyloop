# 版本化冒烟执行器

对应引擎 0.1.0、helper API 1、模板版本 1（2026-10-21）。项目通过受保护薄执行器声明 build/start/ready/run_case/cleanup；引擎将新 argv 的首项解析为受信解释器 `-I -B` 和绝对 launcher。旧 `{python}` 与 vendored harness 继续兼容，不自动迁移。

## 从草案到锁定

目标须为未初始化、干净、有名分支的 Git 根：

```bash
storyloop init /path/project --scaffold --stack python --json
storyloop init /path/fullstack --scaffold --stack maven --stack node --json
```

生成执行器、`acceptance/AGENT_GUIDE.md`、`acceptance/storyloop-settings.json`、`acceptance/onboarding.json`。该步骤不执行构建/测试、不安装依赖、不改 Git exclude、不写 `.storyloop`、不提交。成功只表示“未初始化、需人工补全”。多栈/未知且没显式选择时返回 `SCAFFOLD_SELECTION_REQUIRED`（2）；Gradle/未知选 `generic`，没有特化支持。

清单记录精确 engine_version、helper_api、template_version、stacks、初始 generated 摘要、reviewed 标志、case_roots 和 executors。摘要用于生成幂等与冲突检测，不阻止后续人工补全。再次生成仅接受完全相同草案，有人工修改则拒绝覆盖。

按实际项目填写 settings 的 unit/check 命令、module/test roots、集成点和 scratch；实现必需 hook。无须执行的 build/start/ready/cleanup 显式设 `status: not_applicable` 并给非空 reason；run_case 必须 implemented。reviewed.settings/guide 经审阅设 true。不要把产物放进 mutable/test/generated roots 或 case_roots；不读取环境/凭证文件来填模板。

提交全部接入文件后，调用原锁定入口：

```bash
storyloop init /path/project --settings /path/project/acceptance/storyloop-settings.json --json
```

init 和后续 BASE 均校验完整性、版本、真实实现函数和保护范围；占位、坏语法、版本不符返回 2。init 用独占锁写控制面，故障仅回滚仍匹配本次内容的写入，保留预存/并发修改。init 成功仍需 readiness、skills/survey 及原审批流程。

## 调用与计数

在既有 manifest 的 command 中使用：

```json
["{storyloop_harness}", "acceptance/tools/python_smoke.py", "acceptance/smoke/test_value.py"]
```

这是一条由引擎解释的 argv，不能直接交给 shell。Windows/Linux 都先安装精确同版本引擎；缺失时失败，不网络 fallback。只执行显式指定 case，空、重复、越界、链接/硬链接或无有效测试都返回 2。executor 的 `source_files` 只同步明确列出的安全文件；`dependency_caches` 只复制显式依赖归档，不共享可写构建目录或扫描凭证。

| 结果 | 退出码 | 证据 |
|---|---|---|
| 实际执行且全绿 | 0 | executed > 0，failed/errors 均为 0 |
| 业务断言失败 | 1 | 可信 TestResult 实际观察到 failure |
| 环境/启动/构建/导入/协议/清理故障、零执行或全部跳过 | 2 | errors > 0 或 executed = 0；错误优先于断言失败 |

最终 JSON 记录由可信 launcher 发出，不能用业务 stdout、自写计数 JSON 或 `os._exit(0)` 证明通过。仅内置按文件运行 unittest 的可信裁判；其他 runner 必须由受保护适配器解释真实执行数、断言失败和环境错误，未知结果返回 2，不能硬编码 Result 假绿。

## 可信 case 与业务子进程

helper 和可信 case 都不能把候选放入自己的 sys.path，也不能直接 `import app`。业务依赖在独立子进程里加载；可信 case 检查协议后断言业务行为。下面是一个无需第三方包的例子：

`app/main.py`（业务子进程）：

```python
import json
print(json.dumps({"value": 1}))
```

`acceptance/smoke/test_value.py`（受保护 case）：

```python
import json
from pathlib import Path
import subprocess
import sys
import unittest
from storyloop.testing.smoke_harness.v1 import HarnessError


class ValueCase(unittest.TestCase):
    def test_value(self):
        try:
            child = subprocess.run(
                [sys.executable, "-I", "-B", str(Path.cwd() / "app/main.py")],
                capture_output=True, text=True, timeout=5)
            if child.returncode != 0:
                raise HarnessError("Business subprocess failed")
            payload = json.loads(child.stdout)
            if (not isinstance(payload, dict) or set(payload) != {"value"}
                    or type(payload["value"]) is not int):
                raise HarnessError("Business protocol is invalid")
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            raise HarnessError("Business subprocess/protocol unavailable") from error
        self.assertEqual(payload["value"], 1)
```

环境失败抛 HarnessError 得 2；业务返回合法 value=2 时，实际断言失败得 1。不要用 `self.assertEqual(child.returncode, 0)` 把缺依赖、崩溃或坏协议当有效 RED。业务伪造 harness 输出也不是合法业务 JSON。

薄执行器的 run_case 和 run：

```python
def run_case(context, case):
    from storyloop.testing.smoke_harness.v1 import python_cases
    return python_cases(context.project, [case], cwd=context.scratch.mirror)


def run(context):
    from storyloop.testing.smoke_harness.v1 import run_hooks
    return run_hooks(context, run_case=run_case)
```

本例 executor.source_files 为 `["app/main.py"]`，其余四阶段必须声明 not_applicable 与真实理由；不创建一个假的 build/start/ready。实际服务项目需实现相应阶段并使用 Context.start_service 登记所有服务，配置明确 HTTP 预期状态，最后清理。健康 4xx/5xx 不默认就绪，服务提前退出或清理失败得 2。

## API 与生命周期

完整签名与职责见 `storyloop/testing/smoke_harness/v1.py`：validate_cases、python_cases、Context/run_hooks、Result、Scratch、run_command、Service、wait_http、free_port。python_cases 的 timeout 是协作式套件预算，manifest 的 timeout_seconds 才是跨平台硬进程期限。

每次调用使用私有 scratch 租约，按内容同步源并处理删除；同大小/mtime 的修改也更新。缓存缺失返回 2，不下载。构建和业务只写 scratch；finally 回收登记服务整棵进程树与本次租约，失败/超时不删除其他调用的资源。free_port 只是候选端口，绑定竞争仍是启动错误。

run_command 接受原生 argv，拒绝 shell 和 Windows .cmd/.bat/.ps1 包装器；构建工具必须离线可用。该函数只用于基础设施，任意非零都归环境错误并抛错，返回受限成功元数据，不返回任意构建日志。业务响应由可信 case 用显式协议读取，不能拿构建输出当判据。

这是执行/证据边界，不是操作系统级恶意代码沙箱；项目执行器和 case 仍须经原审批与保护流程。helper 不提供数据库、鉴权、登录客户端或业务断言模板，不承诺真实复杂仓库接入耗时。

## 旧仓库迁移与回退

已有 baseline 时禁止 scaffold。可在独立公开/安全副本生成和审查草案，再在原项目通过 `base-propose → 精确路径独立提交 → base-update` 引入清单、薄执行器和所需清单命令；BASE 同样拒绝未完成的新清单。升级精确引擎版本/helper 记录也走此流程，不能只改元数据绕过回归。回退到旧 vendored 执行器是另一次独立 BASE，需保留原可用安装环境并回归旧 case。
