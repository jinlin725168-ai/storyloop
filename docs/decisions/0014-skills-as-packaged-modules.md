# 0014 摸底做成引擎自带的技能包，装进项目供 Claude Code 与 Codex 通用

日期：2026-09-23

## 问题

决定 0013 把摸底写成 survey 角色的一段提示词。用户指出：摸底的方法应当像本地的知识库沉淀技能（project-genesis 那类"章节模板 + 质量标准 + 阶段门禁"的文档系列做法）一样，是一个可以初始化到任何项目、独立维护、Claude Code 与 Codex 都能用的技能模块，而不是埋在引擎代码里的字符串。

## 决定

- 技能包源在引擎仓库 `storyloop/skills/<name>/`：`SKILL.md`（frontmatter：name、version、description）加 `references/`（八份文档的章节骨架与质量标准、按技术栈的探查清单）。随引擎发布（package-data）。
- `storyloop skills <project>` 把每个技能复制到 `.claude/skills/<name>/` 与 `.codex/skills/<name>/`，两种工具各自自动发现；幂等，报告打包版本与已装版本。已初始化的项目自动开 BASE 提案，技能文件作为底座受保护。
- 引擎的 survey 角色不再自带长说明：`instructions_for` 把技能文本（项目副本优先，否则打包副本）附在简短的角色契约后，command / codex / manual 三种适配器拿到同一份。
- 技能写明两种运行方式：引擎调用（只输出 JSON）与交互式（直接写文件并提示走 BASE）。

## 理由

- 一份来源、两种入口：人在会话里用和引擎自动用产出一致。
- 独立维护：改摸底方法只动技能包，不动引擎逻辑；项目可本地修订副本。
- 适配器中立：技能文本进请求，不依赖某个工具的技能加载机制。

## 代价

- 项目里有两份相同副本（Claude、Codex 各一）；`storyloop skills` 负责同步。
- 技能文本进每次 survey 请求，约十几 KB，可接受。
