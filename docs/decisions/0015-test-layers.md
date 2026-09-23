# 0015 五层测试各在其环节

日期：2026-09-23

## 问题

引擎里叫"冒烟"的东西其实是每个变更的验收套件；真正的系统级核心功能冒烟没有；实现前没有任何机制防止改坏既有行为；单元与集成混在一条回归命令里；静态检查不在闭环里；`ui_regression` 挂在完成阶段，一次把合并拖慢了一小时。

## 决定

| 层 | 名字 | 位置 | 谁写 | 门 |
|---|---|---|---|---|
| 系统冒烟 | smoke | `acceptance/smoke/`，跨变更累积，受保护（`SMOKE_PROTECTED`） | acceptance 角色 `smoke-propose` 提议，人 `smoke-apply` | 每轮尝试、`ci`、`base-update` |
| 变更验收 | acceptance | `acceptance/changes/<id>/`（旧仓库的 `acceptance/smoke/<id>/` 继续识别） | acceptance 角色盲写，先红 | 每轮尝试、`ci` |
| 特征化 | characterize | `test_roots` | characterize 角色；在旧代码上跑绿、只改测试文件才开工；实现改了未声明的既有文件则还原、补锁、重试 | 机器验证 |
| 单元 / 集成 | unit / integration | `unit_commands` / `integration_commands`，代码里分目录或包 | implement 角色（单元 TDD） | 每轮尝试、`base-update`；各自出结论 |
| 静态检查 | checks | `check_commands` | 项目配置 | 每轮尝试 |

每轮尝试顺序：实现 → 边界审计 → 未锁定路径检查 → unit → integration → acceptance → smoke → checks → review。报告的"分层验证"一节各层一行。`ui_regression` 撤掉；Playwright agents 只作为冒烟维护草案阶段的项目工具。

## 理由

- 用户的分层：核心功能是最基础的保障、验收面向用户、单元先红后绿加特征化锁定、集成再后、CI 的扫描与风格最后；每层放在它能起作用的环节，harness 的保证由各层分担。
- 系统冒烟是全系统的保证，代理只能提议，人批准。
- 特征化门是五层里最能防"改坏既有功能"的一道，值得机器验证。

## 代价

- 一个变更多一次 characterize 调用和一次单元回归。
- 老仓库要做一次迁移（settings 拆层、建立系统冒烟基线、测试目录分包）。
