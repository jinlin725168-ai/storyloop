# 0010 证据与人工验收绑定实现指纹，引擎产物不算

日期：2026-09-22

## 问题

人工验收记录和冒烟证据原来绑定整棵树的指纹。完成阶段引擎自己会写 design.md、活规格、UI 资产、报告，一旦中途崩溃，再次运行时指纹已变，证据全部失效，只能人工迁移。

## 决定

两种指纹：
- `candidate_digest`：Git 可见文件减去 `acceptance/ui/`，用于冒烟证据与候选副本的绑定。
- `implementation_digest`：Git 可见文件再减去引擎产物（`openspec/changes/<id>/design.md`、`decisions.md`、`openspec/specs/`、`acceptance/ui/`、`reports/storyloop/<id>/`），用于人工验收记录和报告门。

完成阶段开始时丢弃上次崩溃留下的引擎产物并重新生成；实现提交已存在则采纳；文档提交去重。整个阶段幂等。

## 理由

- 人验收的是实现，不是引擎写的文档。
- 崩溃恢复不该需要人手改状态文件。

## 代价

- 引擎产物的路径集合成了引擎的一部分（`_engine_output_roots`），新增产物类型时要同步维护。
