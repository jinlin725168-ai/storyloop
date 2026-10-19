# Task 1 报告：调用级技能来源与完整费用

## 更改

- 新增 `instruction_skill_snapshot`，一次读取实际技能文本并返回稳定路径、版本和内容 SHA-256；角色指令只构造一次，同一冻结文本交给 adapter，并把同批快照写入调用账本。
- adapter `_telemetry.skills` 在写账本前丢弃，由引擎快照覆盖；无技能角色显式记录空列表，旧账本缺少该字段仍保持可区分。
- telemetry 白名单新增 `vendor`、`tier_applied` 和引擎技能快照；数值拒绝 bool、负数、NaN 和 Infinity，字符串有长度上限。
- 新增纯聚合模块，费用、input/output tokens 和 turns 分别计算 coverage；仅覆盖全部调用时给出完整总计，费用另保留 `known_cost_usd`。总计和每角色均包含 calls、failed、wall、模型、厂商、档次和技能。
- schema 或进程退出失败前若已有可解析 telemetry，会保留实际 usage；Codex JSON event usage 也通过 trace 保留。

## RED

命令：

```text
/home/jinlin/work/storyloop/.venv/bin/python -m unittest tests.test_report_call_provenance tests.test_telemetry -v
```

结果：失败（exit 1）。新测试模块因 `instruction_skill_snapshot` 不存在而导入失败；原有 5 个 telemetry 测试通过，证明失败来自缺失的新接口。

## GREEN

命令：

```text
/home/jinlin/work/storyloop/.venv/bin/python -m unittest tests.test_report_call_provenance tests.test_telemetry tests.test_project_agents -v
```

结果：14 tests，全部通过（exit 0，18.482s）。另执行 `python -m py_compile` 覆盖 4 个生产模块和新测试文件，以及 `git diff --check`，均通过。

未运行全仓测试；controller 明确要求仅运行 Task 1 目标测试，最终全量由 controller 统一执行。

## 自审

- API 签名保持不变；`instructions_for` 仍返回字符串，三个私有 adapter 签名未变。
- 快照哈希覆盖实际传递的完整技能文本（含 references），不是结束时重新读取磁盘；测试验证两次调用之间内容变化会保留两个不同哈希。
- 新聚合保留旧顶层和 per-role 键，并新增同构字段；失败调用包含在 coverage 分母和 failed 计数中。
- 测试曾错误修改 packaged fallback 技能文件；已改为临时项目本地技能夹具并恢复文件，最终 diff 不包含技能内容改动。
- 新文件和修改文件均低于 500 行，新增函数低于 50 行；无新依赖、外部服务、敏感文件读取或 API 签名变更。

## 疑虑与限制

- command adapter 只有在 stdout 已包含可解析 JSON `_telemetry` 时，才能在非零退出后恢复 usage；若进程在输出结构化 telemetry 前崩溃，账本只能保留墙钟、失败状态和引擎技能快照，缺失维度按 unknown 统计。
- 旧账本没有调用级技能字段，保持字段缺失；无法从最终磁盘状态倒推出历史技能来源。

## 提交

提交：`feat(reports): capture call provenance and complete cost coverage`（本报告随该提交提交，最终哈希见任务结果）。
