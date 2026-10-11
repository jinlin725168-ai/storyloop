# 通用工程模式演示

正式流程直接作用于一个已有的干净 Git 仓库：

```powershell
storyloop init C:\work\my-project
storyloop skills C:\work\my-project            # 可选：装 foundation-survey 技能包
storyloop survey C:\work\my-project            # 可选：摸底产出知识库，提交后 base-update
storyloop change C:\work\my-project --idea "增加一个可观察的业务能力"
storyloop approve C:\work\my-project --change FEAT-YYYYMMDD-001
storyloop report C:\work\my-project --change FEAT-YYYYMMDD-001
```

仓库不会附带固定语言的演示代码，因为通用模式会探测目标项目自身的构建与测试入口。可重复、无需模型的完整示例位于 `tests/test_product_e2e.py`：它创建临时 Git 工程，锁定底座，生成变更验收套件，观察真实 RED，写入业务模块，生成追溯报告并验证本地 `--no-ff` 合并；整个过程还会验证原有 CI 文件字节不变。
