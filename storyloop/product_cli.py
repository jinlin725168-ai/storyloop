"""Product change creation and product command orchestration."""
from datetime import datetime, timezone
from pathlib import Path
import sys

from .acceptance_repair import repair_smoke
from .answers import pending_questions, record_answers
from .approvals import approve_change
from .change_log import change_log
from .changes import create_change, load_change, revise_change
from .product_contract import SLUG
from .guarded_calls import read_only_call
from .manual_acceptance import record_manual_acceptance
from .models import StoryLoopError, require
from .next_steps import append_pending_memory, draft_next, next_step, supersede_revision_next
from .openspec_docs import document_change
from .routing_report import draft_stop, report_routing
from .run_recovery import reset_run
from .stop_report import stop_of
from .telemetry import adopt_pending, summarize
from .product_contract import validate_product
from .requirement_checklist import blocking_items, evaluate_checklist
from .semantic_repair import answer_with_repair


def change(project: Path, args, product_answer, revise_with_agent,
           clarify_interactively, draft_result, is_product_project, invoke_project_agent) -> dict:
    """Create or revise a product contract using the caller's interaction functions."""
    require(is_product_project(project), "Run storyloop init on this Git project first")
    from .adapter_capabilities import ensure_checked
    ensure_checked(project)
    interactive = not getattr(args, "no_interactive", False)
    if args.supersede:
        require(args.message and not args.idea and not args.change_id,
                "--supersede needs --message (and no --idea / --change)")
        from .supersede import supersede_change
        from .guarded_calls import read_only_call
        reviser = lambda payload: product_answer(  # noqa: E731
            project, payload,
            call=lambda request: read_only_call(
                project, args.supersede,
                lambda: invoke_project_agent(project, "product", request)))
        created = supersede_change(project, args.supersede, args.message, reviser,
                                   reason=args.reason)
        adopt_pending(project, created["change_id"])
        state = clarify_interactively(project, created["change_id"], revise_with_agent, interactive)
        from .requirement_checklist import blocking_items, evaluate_checklist
        from .next_steps import draft_next
        pending = state["product"]["questions"] or blocking_items(
            evaluate_checklist(state["product"], project))
        return draft_result(project, state, draft_next(project, state) if pending else created["next"])
    require(not args.reason, "--reason is only valid with --supersede")
    if args.idea:
        require(not args.change_id and not args.message,
                "Use --idea for a new change, or --change with --message for a revision")
        from .readiness import require_readiness
        require_readiness(project)
        product = product_answer(project, {"idea": args.idea})
        from .change_types import definition_for
        from .product_contract import nature_of
        prefix = definition_for(nature_of(product)).id_prefix
        slug = choose_slug(args, product.get("slug"), interactive and sys.stdin.isatty(),
                           prefix=prefix)
        created = create_change(project, args.idea, product, slug=slug)
        adopt_pending(project, created["change_id"])
        state = clarify_interactively(project, created["change_id"], revise_with_agent, interactive)
        return draft_result(project, state)
    require(args.change_id and args.message,
            "Revision needs both --change and --message")
    revise_with_agent(project, args.change_id, args.message, [])
    state = clarify_interactively(project, args.change_id, revise_with_agent, interactive)
    return draft_result(project, state)


def choose_slug(args, proposed: str | None, interactive: bool, ask=input,
                *, prefix: str = "FEAT") -> str | None:
    """Choose a safe ID suffix from the flag, human answer, or agent proposal."""
    explicit = getattr(args, "slug", None)
    if explicit is not None:
        require(SLUG.fullmatch(explicit) is not None, "Invalid --slug")
        return explicit
    proposed = proposed if isinstance(proposed, str) and SLUG.fullmatch(proposed) else None
    if not interactive:
        return proposed
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    prompt = f"将创建变更 {prefix}-{day}-{proposed or '<功能短名>'}，直接回车确认，或输入新的功能短名："
    for _ in range(2):
        answer = ask(prompt).strip()
        if not answer:
            return proposed
        if SLUG.fullmatch(answer):
            return answer
        prompt = "功能短名须为 3–40 个小写字母、数字或连字符，请重新输入："
    raise StoryLoopError("Invalid feature slug; use --slug to choose a valid value")


def dispatch(project: Path, args, run_with_answers, draft_result, load_run_state) -> dict:
    """Handle product commands after the entrypoint has checked project readiness."""
    change_id = getattr(args, "change_id", None)
    if args.command == "approve":
        require(change_id, "Product approval needs --change")
        approve_change(project, change_id)
        return run_with_answers(project, change_id, interactive=not args.no_interactive)
    if args.command == "run":
        require(change_id, "Product execution needs --change")
        return run_with_answers(project, change_id, interactive=not args.no_interactive,
                                overrule=getattr(args, "overrule", None))
    if args.command == "answer":
        return record_answers(project, change_id, args.answers)
    if args.command == "status":
        require(change_id, "Product status needs --change")
        change = load_change(project, change_id)
        run_path = project / ".storyloop/state/runs" / f"{change_id}.json"
        run = load_run_state(project, change_id) if run_path.exists() else None
        return {"status": (run or {}).get("status", change["status"]), "change_id": change_id,
                "revision": change["revision"], "run": run,
                "stop": stop_of(project, run) if run else draft_stop(project, change),
                "routing": report_routing(project, change),
                "telemetry": summarize(project, change_id),
                "next": append_pending_memory(project,
                    draft_result(project, change)["next"] if run is None and change["status"] == "DRAFT"
                    else (next_step((run or {}).get("status") or change["status"], change_id, project, run)
                          or ((run or {}).get("stop") or {}).get("resume")))}
    if args.command == "log":
        return change_log(project, change_id)
    if args.command == "reset":
        return reset_run(project, change_id, args.reason)
    if args.command in {"smoke-repair", "acceptance-repair"}:
        return repair_smoke(project, change_id, args.reason)
    if args.command == "document":
        return document_change(project, change_id, args.design.resolve() if args.design else None,
                               capability=args.capability)
    if args.command == "accept":
        require(change_id and args.criteria, "Manual acceptance needs --change and --criterion")
        return record_manual_acceptance(project, change_id, args.criteria, args.note)
    if args.command == "report":
        require(change_id, "Product report needs --change")
        path = project / "reports" / "storyloop" / change_id / "report.md"
        require(path.is_file(), "Acceptance report is not available")
        return {"status": "REPORT", "change_id": change_id, "report": str(path)}
    raise StoryLoopError(f"Unknown command {args.command}")


def draft_result(project: Path, state: dict, next_action=None) -> dict:
    stop = draft_stop(project, state)
    pending = state["product"].get("questions") or blocking_items(evaluate_checklist(state["product"], project))
    hint = draft_next(project, state) if pending else (stop["resume"] if stop else next_action)
    return {**state, "stop": stop,
            "next": supersede_revision_next(project, state) or hint or draft_next(project, state)}


def revise_with_agent(project: Path, change_id: str, message: str, answers: list[dict],
                      product_answer) -> dict:
    """One product revision: the agent rewrites the contract, the engine records the answers."""
    current = load_change(project, change_id)
    product = product_answer(project, {
        "change_id": change_id, "message": message,
        "answers": [{"question_id": a["question_id"], "question": a["question"], "answer": a["answer"]}
                    for a in answers],
        "current_product": current["product"],
    })
    return revise_change(project, change_id, product, message,
                         clarifications=[{"question": a["question"], "answer": a["answer"]} for a in answers])


def product_answer(project: Path, payload: dict, call, invoke_project_agent) -> dict:
    """Get one semantically valid product answer, allowing one corrective call."""
    from .baseline import _git
    payload = {**payload, "routing_version": 1,
               "source_commit": _git(project, "rev-parse", "HEAD").strip()}
    def validate(value):
        if isinstance(value, dict) and "current_product" not in payload and "slug" in value:
            if not isinstance(value["slug"], str) or not SLUG.fullmatch(value["slug"]):
                value.pop("slug")
        validate_product(value)
        require(isinstance(value.get("assessment"), dict), "Product assessment is required")
    operation = lambda: answer_with_repair(
        call or (lambda request: invoke_project_agent(project, "product", request)), payload, validate)
    return read_only_call(project, payload.get("change_id", "pending"), operation)


def clarify_interactively(project: Path, change_id: str, reviser, interactive: bool,
                          per_round: int, terminal, say, pick_answer) -> dict:
    """Resolve a draft's questions in bounded rounds, preserving the answer record."""
    state = load_change(project, change_id)
    while state["product"]["questions"] and interactive and terminal():
        questions = state["product"]["questions"][:per_round]
        say(f"草案有 {len(state['product']['questions'])} 个待决问题，本轮澄清 {len(questions)} 个"
            "（回车选推荐项，输入序号或自由文本，q 结束本轮）：")
        answers = []
        stopped = False
        for index, question in enumerate(questions, 1):
            say(f"\n[{question['id']}] {question['text']}")
            for number, option in enumerate(question["options"], 1):
                marker = "（推荐）" if option == question.get("recommended") else ""
                say(f"  {number}. {option}{marker}")
            if question.get("why"):
                say(f"  为什么重要：{question['why']}")
            reply = input("> ").strip()
            if reply.lower() == "q":
                stopped = True
                break
            answer = pick_answer(reply, question)
            if answer is None:
                continue
            answers.append({"question_id": question["id"], "question": question["text"], "answer": answer})
        if not answers:
            break
        message = "澄清回答（请据此更新故事、规则和验收条件，删除已回答的问题，并保留 clarifications）：\n" + \
            "\n".join(f"{a['question_id']} {a['question']} → {a['answer']}" for a in answers)
        state = reviser(project, change_id, message, answers)
        if stopped:
            break
    return state


def run_with_answers(project: Path, change_id: str, interactive: bool, runner,
                     overrule: str | None, terminal, say) -> dict:
    """Resume a run after each human answer when a terminal is available."""
    result = runner(project, change_id, **({"overrule": overrule} if overrule is not None else {}))
    while result.get("status") == "NEEDS_INPUT" and interactive and terminal():
        questions = pending_questions(project, change_id)
        if not questions:
            break
        say(f"代理有 {len(questions)} 个问题需要你决定（直接回车表示让代理自行决定）：")
        answers = []
        for index, question in enumerate(questions, 1):
            reply = input(f"[{index}/{len(questions)}] {question}\n> ").strip()
            answers.append(reply or "由人确认：此问题无特别要求，由代理按现有约定自行决定")
        record_answers(project, change_id, answers)
        result = runner(project, change_id)
    return result
