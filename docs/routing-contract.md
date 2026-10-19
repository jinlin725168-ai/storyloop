# 分流实施契约

本页补齐详细计划中的嵌套字段与实施时明确的证据规则；不改变已批准的三档行为。使用流程见 [分流指南](routing-guide.md)。

## 入口与量测

`product.assessment` 在旧 schema 中可缺省，在 routing_version=1 的新协议中语义必需。每个验收条件恰有一个 `{story_id, criterion_id, feasibility, method, existence, evidence}`。

- method 为 `{commands: list[list[str]], steps: list[str], expected: str}`；manual 需可执行步骤，自动判据需可用命令。结构化空值不等于证明。
- evidence 为 `{locations: list[str], search_scope: list[str]}`，位置使用提交内真实 path:line，缺失性允许明确搜索范围，不接受一句“没有”。风险使用 `{value: yes|no|unknown, evidence}`。
- scope 为 `{touched, creates, interfaces, symbols}`；symbols 每项 `{path, symbol}`。角色不能自行宣布 complete。引擎重新量测规模与引用，未覆盖的既有业务文件引用数为 unknown，空 touched 为 0。
- 低风险范围/引用未知可先按 standard 批产品、在 L2 补齐；判据 feasibility/existence 或高风险未知仍在入口 NEEDS_INPUT。L2 未知不能进实现。
- 测试/业务分类若来自未跟踪可信配置，记录 classification 的 workspace_config digest 与 commit_bound=false，参与 assessment 绑定；无配置时以提交树分类。Git 路径输出包含替换字符时保守拒绝。

## 方案与审批

权威方案只在 `state.planning.plan`；量测在 `state.planning.assessment`。Option 字段：

```text
{id, summary, touched, creates, interfaces, symbols, risks, forbidden_paths,
 constraints, regression, migration, dependencies, principle_checks}
```

symbols 覆盖各选项自身 touched 业务文件，不能借主方案证据。constraints 为 `{criterion_id,path,rule}`；migration 为 `{steps,reason}`；dependencies 为 `{ecosystem,name,version,verification:{check_index}}`；principle_checks 逐条引用受信原则 ID。

Plan.binding 包含 product_digest、source_commit、scope_digest、suite_digest、policy_digest、principles_digest、baseline_digest；L3 再含 candidate_digest。source_commit 是 run.contract_commit；scope_digest 覆盖所有选项 ID 与四个 scope 字段。plan.id 排除 id/status/approval 后作 canonical SHA-256。

用户选择引起拆分停点时，`pending_option={plan_id,option_id}` 只表示选择意图，不是批准。continue-heavy 绑定 assessment 和实际选项，之后仍需显式 design approval。失败写入保持原状态，旧 plan ID 不可复用。

## 一致性与信任

只有 heavy 且有效方案批准后调用 review 的 consistency 模式；所有新协议档位都验证依赖证据。consistency 使用独立闭合 schema：verdict 为 coherent/blocked/needs_input，finding 为 `{target,criterion_id,location,blocking,message}`。target 为 contract/oracle/design；引用必须对应真实条件与当前文件行，design 引用仅支持受限 `plan:OPTION_ID/FIELD[/key-or-index]`。非法回答只修复一次，再错就阻断。

混合发现按 contract → oracle → design 路由为 NEEDS_SPEC_FIX → ORACLE_DEFECT → DESIGN_REVISION_REQUIRED；驳回只解除指定归属，其他发现保留。角色只读，routing-plan.json 即使处于 scratch 也受控制面保护；引擎单独提交方案工件。

原则只读受信 baseline.source_head 的 openspec/principles.json，绑定完整 BASE 与原则摘要；正式 BASE 更新可以改变准则，任意候选提交不能替代。`invalidate_consistency(replan=False)` 保留锁，仅清一致性/依赖证据；replan=True 另清方案/评估/continue/pending。判据修复和输入变化不得复用陈旧一致性结论。

依赖支持严格限定为 [分流指南的精确 9 argv 离线 wheel 协议](routing-guide.md#原则和依赖证据)，不承诺一般解析、传递解析、安装、运行或漏洞审计。

## L3 与恢复

新协议 implement/review 可返回可选 `routing_risks: [{risk,statement,location}]`，legacy 无此字段仍可工作。已成立风险 yes 在同一合同/run 内不能被 no 擦除；显式新合同可以按新证据重评估。

planning.origin=L3；phase 顺序为 REPLAN → APPROVAL → CONSISTENCY → CHARACTERIZE → READY。consumed_attempt 保留实现成本；restore 保存路径与 blob/mode 摘要，不含文件正文。已完成恢复在 READY 退休到 restore_history，但这些既有路径仍需有效行为锁。有效 GREEN 锁在重规划/重新批准间复用。

candidate_digest 保留原批准绑定，execution_digest 单独验证获授权实现后的精确候选进展；新 L3 清理 checkpoint。未知中断结果保守停止，不扩展成通用事务恢复。

报告先取当前执行 phase，只有明确处在 planning 时才展示规划子阶段；历史 READY 不覆盖 verify/implement。入口停点不虚构 run/attempt。status 只读并返回真实 run status，阻断可退出 2。

readiness lineage 仅在回执完成且完整证据核验后继承既有 READY，原 approval 对象保持不变。缺失层仍 MISSING，普通配置/HEAD/suite 漂移须重采，未知计数不能伪造成功。
