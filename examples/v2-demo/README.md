# StoryLoop v2 双适配器演示

在仓库根目录执行：

```powershell
python examples/v2-demo/run_demo.py
```

脚本将示例复制到两个临时、相互隔离的项目，分别使用 `agent-a` 与
`agent-b` 跑通 OpenSpec/宪法门禁、RED → Agent → L1 → L2、final Judge
和统一审计指标。它不访问网络、不提交或推送 Git，也不保留临时工作区。
