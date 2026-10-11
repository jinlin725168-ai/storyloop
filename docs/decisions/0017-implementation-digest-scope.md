# 0017 实现指纹排除整个引擎区域，以代码为准修正 0010 的范围

日期：2026-10-11

## 问题

决策 [0010](0010-implementation-digest.md) 写的是：实现指纹 = Git 可见文件减去 `openspec/changes/<id>/design.md`、`decisions.md`、`openspec/specs/`、`acceptance/ui/`、`reports/storyloop/<id>/`。但同一天落地的代码（`smoke_runner.implementation_digest`）排除的是 `project_profile.GENERATED_PATHS` 整组：`openspec/changes/`、`openspec/specs/`、`acceptance/changes/`、`acceptance/smoke/`、`acceptance/ui/`、`reports/storyloop/`。2026-10-11 文档核对时发现两者不一致，需要定一个。

## 决定

以代码为准：实现指纹排除整个引擎区域（`GENERATED_PATHS`）。0010 的其余部分（两种指纹、完成阶段先丢弃再重生成、幂等）不变。

## 理由

- 被排除的每个区域都有自己的保护，不靠实现指纹：
  - 合同（`openspec/changes/<id>/` 的合同部分）由批准时的合同摘要锁定，修订即让批准失效。
  - 变更验收套件（`acceptance/changes/<id>/`）与系统冒烟（`acceptance/smoke/`）由各自的字节摘要保护，实现代理写到它们即判越界。
  - 活规格、报告、design.md、decisions.md 是完成阶段的引擎产物，崩溃后丢弃重生成。
- 范围只排除 design.md 和 decisions.md 时，`smoke-propose` / `smoke-apply` 这类合法地改动引擎区域的操作，或另一个已完成变更的取代记录（`traceability.json` 的 `superseded_by`），都会让人工验收记录无故失效。
- 人验收的是业务代码与它的单元、集成测试；这些都在引擎区域之外，改动仍然会让证据失效。

## 代价

- 引擎区域的路径集合同时决定三件事：实现指纹的排除范围、init 时哪些已跟踪文件不算底座（`baseline._foundation_paths`）、开工前允许出现哪些新的已跟踪文件（`baseline._verify_tracked_scope`）。新增引擎产物目录时要改 `GENERATED_PATHS`，并确认该目录有自己的保护摘要，否则它就成了不受任何指纹约束的区域。
