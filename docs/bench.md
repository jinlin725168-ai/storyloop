# 冻结 Case 评测底座（v1）

每个 Case 放在 `bench/cases/<case-id>/v1/`，包含 `case.json`、已批准合同
`contract.json`、`gold.json` 和 `checks/`。Case 固定 repository_id、Base/Solution
提交、合同及套件哈希。`--repository` 是本机只读来源仓库路径，不属于 Case 身份。
v2 仅预留版本目录；verification_entries 合同待 ①b 后制备。

校准只执行确定性 Gold 检查，不调用代理：

```bash
storyloop bench --json calibrate dogfood3-stash-git-safe --case-version 1 \
  --repository ~/work/storyloop-dogfood3
```

校准记录写入 `.storyloop/bench/calibrations/`。F2P 必须 Base 失败且 Solution
通过；P2P 两侧都必须通过。缺失检查、超时、执行错误或自举 helper 导入遮蔽均记
`FAILED`，不会调整检查语义来通过校准。历史 Case 中的测试进程直接导入
`storyloop.testing.*` 时，必须先把验收套件改造成 `{storyloop_harness}` 受保护
executor，再升版 Case；旧 Case 和已有记录保持原样。

Run 框架冻结 Case 哈希、两侧引擎 commit、适配器、模型、预算和评分合同；每侧两次，
按 Base/Candidate 交错。测试适配器覆盖调度与模拟人规则。真实代理参测默认关闭，
`storyloop bench run` 会提示预计累计 13～22 小时并要求 `--i-accept-cost`；
当前 CLI 仍不接入真实代理。Run 记录位于 `.storyloop/bench/runs/`，可用
`storyloop bench --json report <run-id>` 读取。缺成本等证据或版本、配置不一致时
标 `NOT_COMPARABLE`，业务失败不能由平均分抵消。
