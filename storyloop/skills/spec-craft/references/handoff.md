# 交接

在**目标项目**里创建 `.storyloop/drafts/spec/<slug>/`；`slug` 用 3–40 个小写字母、数字或连字符。写两份 UTF-8 Markdown：`requirements.md` 是含开头评估块的需求文档，`spec.md` 是按规则组织的规格文档。请人先检查和修订。

本批使用现有 CLI，把两份文档的内容交给 `storyloop change --idea`。CLI 实际要求在命令后给项目路径；在项目根目录执行：

```bash
storyloop change . --idea "$(cat .storyloop/drafts/spec/<slug>/requirements.md .storyloop/drafts/spec/<slug>/spec.md)"
```

PowerShell 等价写法：

```powershell
$idea = (Get-Content .storyloop/drafts/spec/<slug>/requirements.md -Raw) + "`n" + (Get-Content .storyloop/drafts/spec/<slug>/spec.md -Raw)
storyloop change . --idea $idea
```

之后按命令行逐个回答尚未澄清的问题，检查引擎生成的合同。`storyloop approve --change` 是省略了项目参数的写法；实际批准命令是 `storyloop approve . --change <变更 ID>`。批准、正式编号和质量检查都在 StoryLoop 里完成，技能不替人批准。

这是第 1 批交接方式；以后由 `storyloop change --from-draft` 取代，当前 CLI 尚无该选项，不要现在调用。
