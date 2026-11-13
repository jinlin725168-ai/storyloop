# 冻结 Case 评测底座（v1/v2）

每个 Case 放在 `bench/cases/<case-id>/v1/`，包含 `case.json`、已批准合同
`contract.json`、`gold.json` 和 `checks/`。Case 固定 repository_id、Base/Solution
提交、合同及套件哈希。`--repository` 是本机只读来源仓库路径，不属于 Case 身份。
v2 目录显式派生自 v1，合同包含 verification_entries，验收 manifest 的测试引用
entry_ids。v2 草案可在批准前校准 Gold；`bench run` 仍要求 v2 合同批准。
同一 v2 目录可声明 `case_revision: "2.1"`：Run 与报告保留该修订号，
v2.1 要求每条自动 AC 的 `entry_ids` 覆盖本故事入口；原 v2 不追加此约束。
不同修订的 Run 不可互比。合同内容变更后须重新取得绑定当前内容哈希的批准记录，
再校准最终冻结字节。

校准只执行确定性 Gold 检查，不调用代理：

```bash
storyloop bench --json calibrate dogfood3-stash-git-safe --case-version 1 \
  --repository ~/work/storyloop-dogfood3 --python /path/to/python3.11
```

校准记录写入 `.storyloop/bench/calibrations/`。F2P 必须 Base 失败且 Solution
通过；P2P 两侧都必须通过。Gold 在各自历史树中原样执行，可导入该树自己的
`storyloop.testing.*`。#104 的导入遮蔽预检只适用于参测引擎的 RED/验收路径。
`case.json` 声明检查用 Python 版本和依赖要求；`--python` 指定解释器，省略时
使用当前 StoryLoop 进程的解释器。校准记录包含实际路径和版本，不同解释器的
记录不可比较。
校准记录的 `contract_approved` 标明校准时合同是否已批准。批准记录会改变合同
字节与 Case 哈希，因此批准后须对最终 Case 再次校准；旧 Case/Run 不变。
v2 批准记录的 `contract_sha256` 绑定规范化 `product` JSON；批准后若产品合同
被修改，Run 拒绝，校准仍执行 Gold 并记录 `approval_stale=true` 与 `FAILED`。
`case.json.contract_sha256` 则哈希整个 `contract.json` 文件，两者都用 SHA-256，
但批准记录不能包含对自身所在文件的哈希。

Run 冻结 Case 哈希、引擎 commit、适配器、模型、预算和评分合同。`--scope base-only`
只执行 Base 引擎两次；`--scope paired` 每侧两次、按 Base/Candidate 交错。每次尝试
在临时目录只拉取 Base commit 的浅层独立仓库（候选树无法读取 Solution 历史），
另检出指定引擎 commit，用已准备依赖的
隔离 venv `--engine-python` 解释器加载该引擎源码。每次 attempt 另建临时 venv，
项目检查使用其 `{python}` 导入候选仓库包；worker 仍优先导入指定引擎源码，
Run 记录分别保留 `engine_python` 与 `attempt_python`。`--repository ID=PATH` 可重复指定 Case
来源；来源仓库不会写入。合同由冻结 JSON 直接创建并以 `bench-frozen-contract`
批准，不调用 product 重写。默认 `--suite-source generated`：引擎正常调用 acceptance
角色从 v2 合同的验证入口生成验收套件、证 RED，并走夹具自动修复流程；Case 冻结
套件只用于 PASSED 后的隐藏 Gold。可显式选 `--suite-source frozen`，在临时树预装
Case 套件，仅隔离评测设计/实现/审查链路。Run 记录包含模式，两种模式不可互比。

真实参测需要 `--i-accept-cost`，随后输入 `yes` 或给 `--yes` 再次确认。开始前会
显示次数、适配器、模型和预计累计耗时，按 Case 数 × 每侧两次 × 单次 40～65 分钟
估算。例如单 Case base-only 为 2 次、约 1.3～2.2 小时。Claude 须另给包含 `{model}`
占位的 `--agent-command`，且该命令需符合 StoryLoop command 适配器 JSON 协议。
合同要求人工验收而 Case 无冻结自动处理规则时，该次尝试记不可比较。设计批准
由模拟人自动处理；非计划回答停在 `NEEDS_HUMAN`。超时及预算
终止记录原因。阶段门槛只接受 generated 模式；v2 的 base-only 要求 5 Case 中至少 4 个各有两次
`PASSED`，且每次非例行停点数不超过 1；不可比较不算通过。

模拟人每次 attempt 最多执行一次非例行纠正：`ORACLE_DEFECT`、`EXISTING_GREEN`
走 acceptance-repair，`DESIGN_REVISION_REQUIRED`、`NO_PROGRESS` 走 replan，
`WORKSPACE_TAINTED` 走 reset；随后继续运行，例行方案批准仍自动处理。第二次纠正停点、
无法合法代答的 `NEEDS_INPUT` 及其他需人工判断的停点直接 `NEEDS_HUMAN`。
纠正理由固定为 `bench simulated human: apply engine stop evidence`，不读取隐藏 Gold，
比真实人工给出的具体理由更保守。Run 记录保留每次停点的脱敏摘要和独立证据目录。
引擎调用抛错时，bench 优先采用持久化 run state 的停机状态；只有状态不可读取时才
回退到异常码或返回值，避免将 `ORACLE_DEFECT` 误记为通用 `BLOCKED`。
受保护 harness v1 的失败用例会在既有 `diagnostics` 列表中附带用例 ID、异常类型和
消息及脱敏的项目内相对路径回溯摘要；RED 停点和验收修复请求会传递这份诊断。

Run 记录位于 `.storyloop/bench/runs/`，可用
`storyloop bench --json report <run-id>` 读取。美元成本缺失时 `cost_accounting`
为 `UNAVAILABLE`，部分覆盖为 `PARTIAL`，完整覆盖为 `COMPLETE`；成本维度单独
标不可比，不阻断业务判定。调用数、代理耗时、墙钟耗时缺失，或版本、关键配置不一致
时仍标 `NOT_COMPARABLE`。readiness 失败会保留分层失败摘要与独立证据路径。
业务失败不能由平均分抵消。
