---
name: oracle-craft
version: 1.1.6
roles: [product, acceptance, design, review]
description: 如何写 StoryLoop 的判据与公共验证入口：行类型、测试有效性、结构断言、执行器退出码。product、acceptance、design、review 角色使用。
---

# 判据写法

判据在实现之前写好，实现者改不了；它必须能证明自己真的运行过、真的有约束力。

## 适用角色

- product：给每条验收条件标 `row_type` 与 `oracle_class`，覆盖七个行类型或写明豁免理由；写消费端矩阵；fix 先追调用链再写根因（每条带 `path:line`），把报告者的猜测单列；`reproduces_defect: true` 只标在"在有缺陷的现有代码上必须失败"的条件上（行类型只能是 positive、degradation、unknown_state、form_parity），回归、状态保持、非目标这三类在修复前本来就成立，不能标；refactor 写可断言的结构目标。
- acceptance：按 `references/acceptance-matrix.md` 写用例；refactor 按 `references/structural-assertions.md` 写结构断言；为每个测试标 `tier`。
- design：核对方案的 `entry_paths` 是否沿已确认入口到达拟改动文件。
- review：检查弱断言（只断言存在、非空）、被删除的测试、用 mock 顶替的集成判据。

## 如何选验证入口

每个新格式故事在合同中声明 `verification_entries`，由人确认。优先选仓库现有最高层公共入口，并写明调用定位 `locator`、可观察结果 `observable` 和选择理由 `reason`；`locator` 是描述，不作为命令执行。验收 manifest 的 `entry_ids` 只引用同故事的入口，方案 `entry_paths` 解释入口如何经过拟修改文件。

- StoryLoop：用公开 CLI `storyloop run <project>`，观察 JSON 的 `status`、`next`，不要直接调用内部路由函数。
- RuoYi：用公开 HTTP `POST /api/system/user`，观察响应状态与业务字段，避免只测 service 私有方法。
- 私有函数、mock 返回值和文件存在性都不能单独充当业务验收入口。

入口缺失或有争议时回到合同澄清，不在验收或实现阶段自造入口。

## 入口上的有效测试

只在合同中已由人确认的入口上设计验收：product 写清行为和可观察结果；acceptance 的每个 manifest test 用 `entry_ids` 关联所属故事入口；design 用 `entry_paths` 说明从该入口到改动文件的路径；review 核对实际调用链。入口 ID、AC 归属和路径关系仍由引擎校验，技能不能放松它。

- 好：经 `storyloop status <project> --change <id> --json` 读取 `stop.options[].action`，用合同给出的字面预期断言某个停点的下一步。
- 坏：只断言文件存在、私有路由函数被调用一次，或根据生产代码同一算法重算 expected；这些不能证明外部行为。
- 新机器可读输出以合同的 `output_contract` 为断言边界：验收精确比较约定位置、字段/段落名、类型与含义，列表/对象覆盖约定的子字段；不要额外规定合同未列的输出格式。约定字段未断言属于过弱，按约定字段断言不属于“超出合同”。
- 尽量经真实公共接口观察；只在外部服务、时钟等系统边界替换依赖，不 mock StoryLoop 自己的守卫、路由或候选行为。测试前提失败须作为夹具问题报告，不能伪装成 RED。

### 准备 / 执行 / 观察

- **准备**：可构造测试仓库、写 `.storyloop` 状态文件，或调用内部 helper 建立前提；这些文件与 helper 须在 manifest `source_files` 中，前提失败要明确报夹具错误。准备动作不充当入口调用。
- **执行**：通过合同声明的入口触发被测行为。CLI 与 `stop_json` 入口在测试子进程中用 `subprocess` 运行公开命令；`public_function` 入口显式导入并调用声明符号。不得用 `product_cli.dispatch` 等内部函数代替 CLI。
- **观察**：经同一声明入口读取结果。CLI 与 `stop_json` 解析公开命令的 JSON 输出并断言字段；公共函数断言其公开返回值或公开可观察效果。不要只检查内部状态或文件存在性。

CLI 入口的最小骨架（`status` 的参数形式以本仓库 CLI 为准；前提准备函数由用例实现）：

```python
import json
import subprocess
import sys

prepare_state(project, change_id)  # 仅准备；所需 helper 列入 source_files
completed = subprocess.run(
    [sys.executable, "-m", "storyloop", "status", str(project),
     "--change", change_id, "--json"],
    check=True, capture_output=True, text=True,
)
status = json.loads(completed.stdout)
assert status["stop"]["status"] == "DESIGN_REVISION_REQUIRED"  # 按合同字面预期替换
```

若已批准入口在准备成本上不可行，报告入口争议并交由 `NEEDS_SPEC_FIX` 路径澄清合同；不得自行绕过入口。引擎的入口校验和停点规则始终优先。

acceptance 提交前自查三项，发现问题先改套件；引擎的确定性校验和自动退回仍是最终规则：

- `regression`、`state_preservation`、`non_goal` 行在实现前必须已通过；若 RED 中失败，检查是否把新行为断言误放在必须保持行。
- manifest 的用例须在声明的 case roots 内；执行器需要的源文件须列入 `source_files`，让隔离镜像能加载。
- `entry_ids` 是引用，不等于调用：CLI/停点 JSON 入口要由测试进程经 `subprocess` 调用声明的公开命令；公共函数入口要显式导入并调用该符号。不要只写注释、字符串或直接调用内部 `dispatch` / `build_stop` 冒充公开入口。

同一 AC 连续被判强度不足时，完整判据规格须一次写明 `inputs`、`preconditions`、已批准 `entry`、`expected`、`negative_boundary` 与 `contract_plan` 对应关系；不得在修复理由中新增合同要求。

## 执行器退出码

- 0：全部通过
- 行为断言失败才可算 RED；unittest `errors`（含前提或夹具错误）不算行为失败，即使脚本退出码是 1 或 2。
- 对 unittest：引擎按汇总行中的 `errors` 识别夹具或环境错误，不要求脚本以固定退出码 2 结束；不得把夹具错误当行为失败。命令无法启动、超时仍是环境错误。

测试工具的汇总行（例如 `Ran N tests`、`N passed`）要保留在输出里，引擎靠它统计执行数。

## 运行环境与判定时点

- StoryLoop 自举 Case：`precondition` 等 helper 只在 manifest 的 `{storyloop_harness}` 受保护 executor 中从当前引擎安装包加载；候选侧普通测试进程不导入 `storyloop.testing.*`，被测业务在候选子进程运行。若 RED 预检报告 `HARNESS_SHADOWED`，按违规文件与修复指引自动重写套件并重证 RED，不把旧 helper 缺少的前提标记当成行为失败。
- 候选副本里没有 .git：验收用例不能调用 git（git diff、git log 等），要比对的内容在用例里自带（例如记录文件摘要）。
- 前提不满足时必须失败并明确报错：读不到文件、命令不可用、环境缺依赖时，绝不降级成"只检查一部分然后通过"，也不得伪装成行为断言失败。降级通过的判据是空心判据，审查会判它不合格。
- 判据在合同提交时的代码上判定：RED 证明（包括 acceptance-repair 之后的重证）用的是合同提交时的代码加当前套件。"必须保持"的条件（regression、state_preservation、non_goal）只能引用那时已经存在的文件，不能引用本次变更才新建的文件（例如本次的特征化测试）。
- 过程约束不写成验收条件：例如"既有测试没有被删除或改写"由引擎的执行数下限与独立审查负责，不写成验收条件，也不写测试去检查。

## 判据与合同的归属（review 使用）

审查发现要标出该谁改：

- `implementation`：代码没做到合同要求。默认值。
- `oracle`：验收用例或特征化测试本身写错了。例如断言与合同不符、依赖候选副本里没有的东西（`.git`、网络、本机路径）、引用了本次变更才新建的文件、前提不满足时降级通过。`location` 指向那个测试文件的行。
- `contract`：按合同做不出来。例如两条验收条件互相矛盾，或者某条条件与必须保持的既有行为冲突。`location` 指向与合同矛盾的代码事实，`criterion_id` 写出冲突的条件。

`oracle` 和 `contract` 必须给出合同里存在的 `criterion_id` 和存在的文件 `location`，否则引擎按实现问题处理。不要因为实现难做就标 `contract`；不要因为测试严格就标 `oracle`。

思路参考 HelixHarness 对应文件。
