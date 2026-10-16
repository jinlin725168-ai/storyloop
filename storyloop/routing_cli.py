"""Explicit routing command validation and dispatch; approval APIs only record."""
from pathlib import Path
from .approvals import approve_change
from .design_approval import approve_design, approve_continue_heavy, replan
from .models import require
from .readiness import inspect_readiness, approve_readiness
from .stop_report import build_entry_stop


BLOCKED = frozenset({'PROJECT_NOT_READY', 'READINESS_APPROVAL_REQUIRED',
                     'EXPLORATION_RECOMMENDED', 'ORACLE_INFEASIBLE',
                     'DESIGN_APPROVAL_REQUIRED', 'DESIGN_REVISION_REQUIRED', 'SPLIT_RECOMMENDED'})


def validate_routing_args(args) -> None:
    """Reject conflicting modes and blank reasons before any agent invocation."""
    get = lambda key: getattr(args, key, None)
    if args.command == 'readiness':
        require(get('change_id') is None, 'readiness forbids --change')
    elif args.command == 'approve':
        modes = [key for key in ('readiness', 'design', 'continue_heavy') if get(key) is not None]
        require(len(modes) <= 1, 'Approval modes cannot be combined')
        mode = modes[0] if modes else 'product'
        allowed = {'readiness': {'readiness', 'reason'},
                   'design': {'change_id', 'design', 'option'},
                   'continue_heavy': {'change_id', 'continue_heavy', 'reason'},
                   'product': {'change_id', 'tier'}}[mode]
        fields = {'change_id', 'readiness', 'design', 'continue_heavy', 'tier', 'option', 'reason'}
        require(all(get(key) is None for key in fields - allowed), 'Conflicting approval arguments')
        require(mode == 'readiness' or bool(get('change_id')), 'Approval needs --change')
        require(get('tier') in {None, 'standard', 'heavy'}, 'Tier must be standard or heavy')
        if mode in {'readiness', 'continue_heavy'}:
            require(isinstance(get('reason'), str) and bool(get('reason').strip()), 'Approval needs --reason')
        for key in modes + (['option'] if get('option') is not None else []):
            require(bool(str(get(key)).strip()), f'--{key} cannot be empty')
    elif args.command == 'run':
        require(get('reason') is None or get('replan'), '--reason requires --replan')
        if get('replan'):
            require(get('change_id') and get('overrule') is None, '--replan needs --change and forbids --overrule')
            require(isinstance(get('reason'), str) and bool(get('reason').strip()), '--replan needs --reason')


def dispatch_routing(project: Path, args) -> dict | None:
    """Dispatch routing modes; return None for commands owned by the original CLI."""
    validate_routing_args(args)
    if args.command == 'readiness':
        result = inspect_readiness(project)
        return _entry(project, result)
    if args.command == 'approve' and getattr(args, 'readiness', None):
        return _entry(project, approve_readiness(project, args.readiness, args.reason))
    if args.command == "approve" and not any(getattr(args, key, None) for key in ("design", "continue_heavy", "tier")):
        return None
    from .cli import run_with_answers
    if args.command == 'approve':
        if getattr(args, 'design', None):
            result = approve_design(project, args.change_id, args.design, option_id=args.option)
            if result.get('status'):
                return result
        elif getattr(args, 'continue_heavy', None):
            approve_continue_heavy(project, args.change_id, args.continue_heavy, args.reason)
        else:
            approve_change(project, args.change_id, tier=getattr(args, 'tier', None))
        return run_with_answers(project, args.change_id, interactive=not getattr(args, 'no_interactive', False))
    if args.command == 'run' and getattr(args, 'replan', False):
        replan(project, args.change_id, args.reason)
        return run_with_answers(project, args.change_id, interactive=not getattr(args, 'no_interactive', False))
    return None


def _entry(project: Path, result: dict) -> dict:
    if result['status'] == 'READY':
        return result
    stop = build_entry_stop(project, result['status'], result)
    return {**result, 'stop': stop, 'next': stop['resume']}
