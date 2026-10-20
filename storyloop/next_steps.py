"""The command that moves a change forward from each stop, so no stop needs the FAQ to read."""

from __future__ import annotations

from pathlib import Path
import shlex
from .routing_report import routing_options


def append_pending_memory(project: Path | str, value: str | None) -> str | None:
    """Append a draft hint while preserving the existing stop/next wording byte-for-byte."""
    from .drafts import list_drafts
    drafts = list_drafts(Path(project))
    pending = [item for item in drafts
               if item.get("kind") in {"lesson", "errata"} and item.get("status") == "pending"]
    invalid = [item for item in drafts if item.get("status") == "invalid"]
    hints = [value] if value else []
    command = f"storyloop drafts {shlex.quote(str(project))}"
    if pending:
        hints.append(f"有 {len(pending)} 个待批经验或勘误草案：{command}")
    if invalid:
        hints.append(f"已跳过 {len(invalid)} 个损坏草案：{command}")
    return "\n".join(hints) if hints else value


def next_step(status: str | None, change_id: str | None, project: Path | str = "<project>",
              details: dict | None = None) -> str | None:
    """A copy-pasteable next action for a run status or error code; None when nothing is pending."""
    details = details or {}
    options = routing_options(project, change_id, status, details)
    if options:
        return options[0]["command"]
    project = shlex.quote(str(project))
    change = shlex.quote(change_id or "<change-id>")
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
    if status == "COST_LIMIT_REACHED":
        return f"提高 .storyloop/config.json 的 limits 后 {run}，继续原候选与同一轮；或作废重开"
    if status == "BUDGET_EXHAUSTED":
        return (f"{log} 看每轮失败原因：验收用例本身有缺陷用 storyloop acceptance-repair {where} "
                f"--reason <原因>；实现问题就提高 .storyloop/config.json 的 max_attempts 后 {run}，"
                f"或另开新变更（运行开始后合同不能修订）")
    if status == "ORACLE_DEFECT":
        return (f"验收判据被判定存在缺陷，请根据停止报告中的证据修正：storyloop acceptance-repair {where} "
                f"--reason <哪条断言写错了> 让 acceptance 角色修正，再 {run}")
    if status == "EXISTING_GREEN":
        return (f"变更验收套件在实现之前就已通过，证明不了 RED：需求若其实已经实现，放弃这个变更；"
                f"若是用例没有断言新行为，storyloop acceptance-repair {where} --reason <原因> "
                f"让 acceptance 角色加强套件并重证 RED，再 {run}")
    return _execution_next(status, where, log, run)


def _execution_next(status, where, log, run):
    if status == "CHARACTERIZATION_FAILED":
        return (f"特征化测试在旧代码上不绿：就地修正 test_roots 里的这些测试（它们只锁定现有行为），"
                f"不用提交；{run} 会重跑单元层，绿了再提交并继续")
    if status == "AGENT_UNAVAILABLE":
        return (f"代理进程没有给出回答（超时、限流、崩溃或 CLI 不可用），这一轮不计入 max_attempts：{log} "
                f"看该次调用的 stderr.log，处理后 {run} 会重试同一轮")
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


def draft_next(project: Path | str, change: dict) -> str:
    """A draft's next step names its open questions and blocking checklist items before suggesting approve."""
    from .requirement_checklist import blocking_items, evaluate_checklist
    source_next = supersede_revision_next(project, change)
    if source_next:
        return source_next
    change_id = change["change_id"]
    where = shlex.join([str(project), "--change", change_id])
    questions = len(change["product"].get("questions") or [])
    blocking = len(blocking_items(evaluate_checklist(change["product"], Path(project))))
    if not questions and not blocking:
        return next_step("DRAFT", change_id, project)
    drafts = f".storyloop/drafts/{change_id}"
    found = []
    if questions:
        found.append(f"{questions} 个待决问题（见 {drafts}/proposal.md 的“待决问题”）")
    if blocking:
        found.append(f"{blocking} 个阻断项（见 {drafts}/checklist.md）")
    return (f"草案还有{'、'.join(found)}，approve 会被拒绝：storyloop change {where} "
            f"--message \"Q1 选推荐项；Q2 …\" 回答或修订，清零后再 storyloop approve {where}")


def supersede_revision_next(project: Path | str, change: dict) -> str | None:
    """Require fresh product evidence on the source branch before replacement approval."""
    link = change.get('supersedes')
    if not link or change['status'] != 'DRAFT':
        return None
    from .baseline import _git
    root = Path(project).resolve()
    source = link['source_branch']
    source_head = _git(root, 'rev-parse', f'refs/heads/{source}').strip()
    stale = (change.get('intake') or {}).get('source_commit') != source_head
    if not stale and _git(root, 'branch', '--show-current').strip() == source:
        return None
    where = [str(root), '--change', change['change_id']]
    lines = [f"旧分支 feature/{link['change_id']} 保留；先提交或暂存其中未提交的改动，再执行：",
             shlex.join(['git', '-C', str(root), 'checkout', source])]
    if stale:
        message = '在当前源分支重新核对范围、风险和验收证据，修订替代合同'
        lines.append(shlex.join(['storyloop', 'change', *where, '--message', message]))
    lines += ['核对修订后的合同，解决入口阻塞和待决问题后，明确批准：',
              shlex.join(['storyloop', 'approve', *where])]
    return '\n'.join(lines)
