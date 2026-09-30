# 贡献指南

## 基本约束

- 只用 Python 标准库，Python 3.11+。不引入第三方运行时依赖。
- 引擎保持技术栈中立。RuoYi 或任何具体底座的逻辑放在目标仓库的 `acceptance/`，不进引擎。
- 单个函数不超过 50 行，单个文件不超过 500 行；复杂逻辑写清楚"为什么"。
- 提交信息用 `type(scope): subject` 中文描述，见根目录 CLAUDE 规范。

## 先写测试

新增功能或修复必须先有一个能复现失败的测试：

```bash
.venv/bin/python -m unittest discover -s tests -t . -v
.venv/bin/python -m unittest tests.test_project_loop tests.test_smoke_repair
```

测试用临时 Git 仓库和脚本化适配器，不需要模型账号和网络。`tests/test_product_e2e.py` 是通用模式的端到端样板。

## 不能破坏的不变量

改动触及以下任何一条时，必须有测试证明它仍然成立：

1. 没有观察到有效 RED 就不调用实现代理；环境错误不是 RED。
2. 合同、冒烟套件、证据文件的字节由指纹保护，篡改即失败。
3. 实现代理不能改 `acceptance/changes/<id>/`、`acceptance/smoke/`、`openspec/changes/<id>/` 的合同部分和报告。
4. 冒烟在隔离候选副本里跑，写入副本即失败。
5. 边界审计覆盖全树，未声明的被忽略文件判 `WORKSPACE_TAINTED`。
6. 引擎不 push、不删分支、不重写历史；CI 文件字节不变。
7. 报告只接受真实命令结果和人工记录，代理文字不是证据。
8. 完成阶段幂等：重复 `run` 不重复调用代理、不重复提交。
9. 任何修订让旧批准失效；人工验收记录与实现指纹绑定。
10. 特征化门：没有 `test_roots` 内的绿测试锁定，实现不能修改既有文件；系统冒烟只经人 `smoke-apply` 更新。

## 模块边界

| 想改什么 | 去哪 |
|---|---|
| 状态机、停点、尝试循环 | `project_loop.py`（持久化在 `project_loop_support.py`） |
| 角色调用与适配器 | `project_agents.py`，返回结构校验在 `project_contracts.py` |
| 变更验收套件、候选副本、指纹 | `smoke.py`、`smoke_runner.py` |
| 系统冒烟与维护 | `system_smoke.py` |
| 边界分类 | `project_boundaries.py`（快照在 `boundaries.py`） |
| 合同、澄清、质量门 | `changes.py`、`approvals.py`、`requirement_checklist.py` |
| OpenSpec 产物 | `openspec_docs.py` |
| 报告 | `change_report.py` |
| 底座 | `baseline.py`、`base_upgrade.py`、`project_profile.py` |
| 命令面 | `cli.py` |

早期独立实例模式与 v2 Stage/DAG 引擎已删除（本地 tag `archive/legacy-modes` 保存了删除前的状态），不要再引入按实例目录或 Stage 配置工作的代码路径。

## 技能包

`storyloop/skills/<name>/` 是随引擎发布的技能，`SKILL.md` 的 frontmatter 必须有 `name`、`version`、`description`；改动内容时升版本号，`storyloop skills` 靠它判断项目里的副本是否过期。技能文本会原样进入代理请求，所以写给代理看，而不是写给人看。

## 文档

行为变化必须同步文档：命令面改动改 README 的命令表和使用指南；协议改动改 `docs/protocols.md`；设计取舍新增一条 `docs/decisions/`。
