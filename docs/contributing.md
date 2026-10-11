# 贡献指南

动手之前读三篇：[modules.md](modules.md)（改哪个模块）、[extension.md](extension.md)（加命令、停点、角色、配置、验证层要动哪些文件，以及不能破坏的不变量）、[building-blocks.md](building-blocks.md)（已有的积木，不要重写）。

## 基本约束

- 只用 Python 标准库，Python 3.11+。不引入第三方运行时依赖。
- 引擎保持技术栈中立。RuoYi 或任何具体底座的逻辑放在目标仓库的 `acceptance/`，不进引擎。
- 单个函数不超过 50 行，单个文件不超过 500 行；复杂逻辑写清楚"为什么"。
- 提交信息用 `type(scope): subject` 中文描述，见根目录 CLAUDE 规范。
- 早期独立实例模式与 v2 Stage/DAG 引擎已删除（本地 tag `archive/legacy-modes` 保存了删除前的状态），不要再引入按实例目录或 Stage 配置工作的代码路径。

## 先写测试

新增功能或修复必须先有一个能复现失败的测试：

```bash
.venv/bin/python -m unittest discover -s tests -t .
.venv/bin/python -m unittest tests.test_project_loop tests.test_smoke_repair
```

测试用临时 Git 仓库和脚本化适配器，不需要模型账号和网络。夹具、测试地图和会被跳过的用例见 [testing.md](testing.md)；改动触及 [extension.md 第七节](extension.md#七禁止事项不变量) 的任何一条不变量，都要有测试证明它仍然成立。

## 技能包

`storyloop/skills/<name>/` 是随引擎发布的技能，`SKILL.md` 的 frontmatter 必须有 `name`、`version`、`description`；改动内容时升版本号，`storyloop skills` 靠它判断项目里的副本是否过期。技能文本会原样进入代理请求，所以写给代理看，而不是写给人看。

## 文档

`docs/` 是本项目的知识库，入口 [docs/README.md](README.md)。行为变化必须在同一个提交里同步文档：

| 改了什么 | 要改的文档 |
|---|---|
| 命令、参数、返回状态、退出码 | README 命令表、[使用指南](product-iteration-guide.md)、[api.md](api.md) |
| 停点或错误码 | [api.md](api.md) 状态表、[FAQ](faq.md)、`next_steps.py` 的提示 |
| 角色、payload、返回结构 | [api.md](api.md) 代理协议、[角色目录](roles.md) |
| settings 或运行参数 | [api.md](api.md) 配置、[接入手册](onboarding-a-repository.md) |
| 新模块、模块职责或依赖方向 | [modules.md](modules.md) |
| 新能力 | [capabilities.md](capabilities.md)，必要时 [概念手册](concepts.md) |
| 新的通用工具函数 | [building-blocks.md](building-blocks.md) |
| 测试结构或 CI | [testing.md](testing.md) |
| 设计取舍 | 新增一条 `docs/decisions/`，被取代的旧记录在索引里标注 |

每篇知识库文档开头的"对应引擎版本 / 核对日期"在对照代码核对后更新。
