"""Read-only projections of the routing authorities for CLI and reports."""
from copy import deepcopy
import shlex


from .run_state import ROUTING_STATUSES as STATUSES


def routing_options(project, change_id, status, details):
    """Build literal, executable recovery commands without inventing approvals."""
    planning = details.get('planning') or {}
    plan = planning.get('plan') or {}
    assessment = planning.get('assessment') or {}
    where = [str(project), '--change', change_id] if change_id else [str(project)]
    command = None
    if status == 'READINESS_APPROVAL_REQUIRED':
        command = ['approve', str(project), '--readiness', details.get('digest', '<digest>'), '--reason', '<缺口理由>']
    elif status == 'PROJECT_NOT_READY':
        command = ['readiness', str(project)]
    elif status == 'DESIGN_APPROVAL_REQUIRED':
        command = ['approve', *where, '--design', plan.get('id', '<plan-id>')]
        pending = planning.get('pending_option') or {}
        if pending.get('option_id'):
            command += ['--option', pending['option_id']]
    elif status == 'SPLIT_RECOMMENDED':
        command = ['approve', *where, '--continue-heavy', assessment.get('id', '<assessment-id>'), '--reason', '<不拆分理由>']
    elif status == 'DESIGN_REVISION_REQUIRED':
        command = ['run', *where, '--replan', '--reason', '<方案修订理由>']
    elif (status in {'EXPLORATION_RECOMMENDED', 'ORACLE_INFEASIBLE'}
          or status == 'NEEDS_INPUT' and 'product_digest' in details) and not planning:
        command = ['change', *where, '--message', '<补充可验证需求>'] if change_id else ['readiness', str(project)]
    return [{'action': '明确决定后继续', 'command': shlex.join(['storyloop', *command])}] if command else []


def routing_view(state, change=None):
    """Keep effective tier, decision evidence and active versus retired restore distinct."""
    routing = state.get('routing') or {}
    planning = state.get('planning') or {}
    intake = (change or {}).get('intake') or {}
    if not routing and not intake:
        return None
    return deepcopy({'initial': routing.get('initial_tier'), 'effective': routing.get('effective_tier'),
                     'history': routing.get('history', []), 'rules': routing.get('reasons', []),
                     'budget_source': routing.get('budget_source'), 'limit': routing.get('limit'),
                     'phase': planning.get('phase', 'intake'), 'origin': planning.get('origin'),
                     'consumed_attempt': planning.get('consumed_attempt'),
                     'evidence': (planning.get('assessment') or {}).get('evidence', intake.get('evidence', [])),
                     'manual_decisions': {'product': (change or {}).get('approval'),
                                          'design': (planning.get('plan') or {}).get('approval'),
                                          'continue_heavy': planning.get('continue_heavy')},
                     'restore': planning.get('restore'), 'restore_history': planning.get('restore_history', [])})


def routing_markdown(view):
    """Compact presentation, retaining the complete machine-readable report fields."""
    if not view:
        return []
    import json
    lines = ['', '## 分流与规划', '',
             f"- 初定 / 生效档位：{view['initial']} / {view['effective']}",
             f"- 预算：{view['limit']}（{view['budget_source']}）；阶段：{view['phase']}"]
    for key, label in [('rules', '命中规则与值'), ('history', '升档历史'),
                       ('missing_checks', '未配置项'), ('manual_decisions', '人工决定'),
                       ('evidence', '证据'), ('restore', '活动恢复'), ('restore_history', '已退休恢复历史')]:
        text = json.dumps(view.get(key), ensure_ascii=False).replace('\n', ' ')
        lines.append(f'- {label}：{text}')
    return lines


def report_routing(project, change):
    """Read persisted authorities only; reporting never invokes tests or approvals."""
    from .storage import load_json
    run = project / '.storyloop/state/runs' / (change['change_id'] + '.json')
    state = load_json(run) if run.is_file() else {}
    view = routing_view(state, change)
    if view is not None:
        ready = project / '.storyloop/state/readiness.json'
        record = load_json(ready) if ready.is_file() else {}
        view['missing_checks'] = record.get('gaps', [])
        view['readiness_evidence'] = {key: record.get(key) for key in ('digest', 'binding', 'layers', 'approval')}
    return view


def draft_stop(project, change):
    """Expose draft intake without remeasuring it during a status query."""
    from .stop_report import build_entry_stop
    intake = change.get('intake') or {}
    if intake.get('status') and intake['status'] != 'READY':
        return build_entry_stop(project, intake['status'], {**intake, 'change_id': change['change_id']})
    return None
