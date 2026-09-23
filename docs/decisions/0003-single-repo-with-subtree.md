# 0003 单仓库底座，多个上游用 git subtree 引入

日期：2026-09-17

## 问题

RuoYi 这类底座前后端是两个上游仓库，而一个功能变更同时跨数据库、后端、前端和菜单 SQL。

## 决定

一个 Git 根目录：`backend/`、`frontend/` 用 `git subtree add --squash` 引入，上游同步用 `git subtree pull --squash` 作为一次 BASE 迭代（提案加 `--allow-mutable`）。

## 理由

- 引擎需要一个干净根目录来做快照、分支和 no-ff 合并。
- 一次变更的所有改动在一个提交里可追溯。

## 代价

- 上游克隆必须 unshallow 才能 subtree。
- 上游文件落在可变区时，同步会改动可变区，因此提案需要显式放行。
