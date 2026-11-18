# 交接

在**目标项目**里创建 `.storyloop/drafts/spec/<slug>/`；`slug` 用 3–40 个小写字母、数字或连字符。写两份 UTF-8 Markdown 给人看：`requirements.md` 是含开头评估块的需求文档，`spec.md` 是按规则组织的规格文档。请人先检查和修订。

再在同一目录写两份机器文件交给 StoryLoop：

- `contract.json`：合同草稿，结构与 `storyloop change` 产生的产品合同相同（`version`、`goal`、`non_goals`、`questions`、`stories`，可选 `assumptions`、`nature` 等）。每条验收条件用 `rule` 写它例证的是本故事第几条规则，用 `polarity` 写 `positive` 或 `negative`；`given` 写具体数据。假设写进 `assumptions`，人确认过的标 `confirmed`，否则 `assumed`。修改已有功能时，故事的 `requirement`、`rule_ids` 写需求登记里的已有 ID（`storyloop requirements <项目>` 可查）。不写 `assessment`：分流评估由 StoryLoop 补。
- `record.json`（可选）：本次共创的经过，例如问了哪些问题、人选了什么、哪些假设被推翻。StoryLoop 会原样存为 `source: agent`，只作参考。

评估三问的结论要由人在 StoryLoop 里确认：先在项目根目录执行

```bash
storyloop assess . --idea "<一句意图>" --problem "<真实发生的一次>" --success "<凭什么知道解决了>" --if-not "<不做会怎样>" --decision do
```

结论是 `drop` 就停下，不创建变更；是 `do` 再交接：

```bash
storyloop change . --from-draft .storyloop/drafts/spec/<slug>
```

StoryLoop 保留草稿里的目标、故事、规则、例子和假设，只补分流评估和验证入口；还有未决的地方会在命令行里问。之后检查引擎生成的合同，批准用 `storyloop approve . --change <变更 ID>`。批准、正式编号（需求、规则、例子的稳定 ID）和质量检查都在 StoryLoop 里完成，技能不替人批准；还有 `assumed` 的假设时批准会被拒。
