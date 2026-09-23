# 测试分层重构计划（2026-09-23）

用户确认的分层与顺序：1 命名 → 5 系统冒烟 → 2 特征化门 → 7 冒烟维护（撤 ui_regression）→ 6 静态检查；单元与集成测试必须分开（命令与代码包级都分开）。

| 层 | 引擎里的名字 | 位置 | 谁写 | 什么时候跑 | 是不是门 |
|---|---|---|---|---|---|
| 1 系统冒烟 | smoke | `acceptance/smoke/`（项目级，跨变更累积，受保护） | 冒烟维护步骤提议、人批准 | 每轮尝试、`ci`、`base-update` | 是 |
| 2 变更验收 | acceptance | `acceptance/changes/<id>/`（旧仓库的 `acceptance/smoke/<id>/` 继续识别） | acceptance 角色盲写，先红 | 每轮尝试、`ci` | 是 |
| 3 前置：特征化测试 | characterize | 项目的单元测试布局（`test_roots`） | characterize 角色，在旧代码上跑绿 | RED 之后、实现之前一次 | 是（机器验证：只改测试文件、单元命令绿、实现只能改已声明路径） |
| 3 单元测试 | unit | 项目布局，`unit_commands` | implement 角色 TDD | 每轮尝试、特征化门、`base-update` | 是 |
| 4 集成测试 | integration | 项目布局（独立目录或包），`integration_commands` | implement 角色 | 每轮尝试、`base-update` | 是 |
| 5 静态检查 | checks | `check_commands`（lint、格式、安全扫描） | 项目配置 | 每轮尝试 | 是 |
| 5 独立审查 | review | 角色 | review 角色 | 每轮尝试 | 是 |

每轮尝试的顺序：实现 → 边界审计 → unit → integration → acceptance → smoke → checks → review。
撤掉 `ui_regression`；Playwright agents 改为冒烟维护步骤里项目自备的工具。
