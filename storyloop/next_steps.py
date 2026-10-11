"""The command that moves a change forward from each stop, so no stop needs the FAQ to read."""

from __future__ import annotations

from pathlib import Path


def next_step(status: str | None, change_id: str | None, project: Path | str = "<project>",
              details: dict | None = None) -> str | None:
    """A copy-pasteable next action for a run status or error code; None when nothing is pending."""
    details = details or {}
    change = change_id or "<change-id>"
    where = f"{project} --change {change}"
    log = f"storyloop log {where}"
    run = f"storyloop run {where}"
    if status == "NEEDS_INPUT":
        return (f"在终端直接 {run} 会逐条提问；或 storyloop answer {where} --answer <回答>（按问题顺序），"
                f"再 {run}")
    if status == "MANUAL_ACCEPTANCE_REQUIRED":
        return (f"逐条人工核对后 storyloop accept {where} --criterion <ID> --note <核对了什么>，"
                f"全部记录后 {run}")
    if status == "BASE_CHANGE_REQUIRED":
        proposal = details.get("base_proposal")
        named = f"（提案 {Path(str(proposal)).stem}）" if proposal else ""
        return (f"底座改动要独立迭代{named}：在源分支单独提交提案里的路径，storyloop base-update {project} "
                f"--proposal <BASE-ID>，把源分支合进功能分支，再 {run}")
    if status == "BOUNDARY_VIOLATION":
        return (f"代理写了不允许的路径：{log} 看是哪次调用写的；还原这些文件（或把目录加进 module_roots / "
                f"test_roots 走一次 BASE 迭代），再 {run}")
    if status == "WORKSPACE_TAINTED":
        return (f"git status 找出非预期改动（残留的 dev server、未声明的构建产物要加进 scratch_paths、"
                f"代理执行了 git add），清理后 storyloop reset {where} --reason <原因>，再 {run}")
    if status == "BUDGET_EXHAUSTED":
        return (f"{log} 看每轮失败原因：验收用例本身有缺陷用 storyloop acceptance-repair {where} "
                f"--reason <原因>；实现问题就提高 .storyloop/config.json 的 max_attempts 后 {run}，"
                f"或另开新变更（运行开始后合同不能修订）")
    if status == "EXISTING_GREEN":
        return (f"变更验收套件在实现之前就已通过，证明不了 RED：需求若其实已经实现，放弃这个变更；"
                f"若是用例没有断言新行为，storyloop acceptance-repair {where} --reason <原因> "
                f"让 acceptance 角色加强套件并重证 RED，再 {run}")
    if status == "CHARACTERIZATION_FAILED":
        return (f"特征化测试在旧代码上不绿：就地修正 test_roots 里的这些测试（它们只锁定现有行为），"
                f"不用提交；{run} 会重跑单元层，绿了再提交并继续")
    if status == "AGENT_CONTRACT_INVALID":
        return f"代理的回答不符合角色结构：{log} 查看该次调用的 response 与 schema_errors，再 {run} 重试"
    if status == "DRAFT":
        return (f"继续修订 storyloop change {where} --message <意见>；满意后 storyloop approve {where}")
    if status in {"APPROVED", "PREPARING", "CONTRACT_READY", "CONTRACT_COMMITTED", "IMPLEMENTING",
                  "VERIFIED", "MERGED", "RECEIPTING", "FINALIZING"}:
        return f"{run} 继续（中断后续跑是安全的）"
    if status == "PASSED":
        return (f"报告：storyloop report {where}；可选：storyloop smoke-propose {where} "
                "把核心链路并入系统冒烟")
    if status in {"BLOCKED", "FAILED", "ERROR", "INTERRUPTED"}:
        return f"{log} 查看最近的代理调用与失败原因；处理后 {run} 可续跑"
    return None
