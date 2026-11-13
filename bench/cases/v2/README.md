# v2 Case 草案：待人批准

五个 `bench/cases/<case-id>/v2/` 从各自 v1 显式派生。来源区间、需求与 Gold
检查命令不变；合同增加故事级 `verification_entries`，manifest 为每个测试标明
`entry_ids`。`verification-evidence.json` 将每个入口连到参考 solution 的文件、行号
与原文。v1 Run 保持原状，也不能与 v2 Run 混比。

v2 合同刻意不含 `approval`，本目录没有批准记录。合并前须由人确认 PR 中的入口表；
确认后再按批准流程冻结并重新校准。当前 `bench calibrate --case-version 2` 会在
`storyloop/bench_cli.py` 的 v1 专用门禁处返回 `BLOCKED`，尚未执行 Gold；
`storyloop/bench_case.py` 也只接受 v1 与已批准合同。这是引擎后续兼容工作，
不能把本草案标为 `CALIBRATED`。

在明确指定 StoryLoop 本地 Python 3.11.15 解释器后，使用现有校准器的快照、
套件覆盖与 Gold 执行步骤做了**非正式预检**：五个 Case 共 21 项检查均符合
F2P（Base 失败、Solution 通过）或 P2P（两边通过）。预检不产生批准或正式校准记录；
正式校准结果仍为 `BLOCKED`。

验收测试的原始断言文件保持字节不变。每个 manifest 测试改用
`{storyloop_harness}` 调用受保护 executor，旁边的 `bench_*.py` 只负责把原有
方法或测试类选择器映射给受保护 unittest，并核对原始文件 SHA-256。
Gold 检查保留 v1 命令，按 #105 在各自历史树中运行，不应用 #104 的遮蔽预检。
