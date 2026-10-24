"""Seven real local lifecycle scenarios; all agents are deterministic test doubles."""
from contextlib import contextmanager
from unittest.mock import patch
from dataclasses import replace
from datetime import datetime, timezone

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project, load_project_config
from storyloop.project_loop import LoopServices, run_change
from storyloop.storage import write_json
from tests.routing_support import legacy_create_change, routed_case
from tests.support import product
from tests.test_baseline_behavior import git, repository
from tests.test_project_loop import smoke_payload

NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)


@contextmanager
def fixture_git_policy():
    """Pin LF in synthetic repositories before their first tracked files are written."""
    from tests import test_baseline_behavior
    original = test_baseline_behavior.git
    def git_with_lf(root, *args):
        result = original(root, *args)
        if args[0] == "init":
            original(root, "config", "core.autocrlf", "false")
        return result
    with patch.object(test_baseline_behavior, "git", side_effect=git_with_lf):
        yield


def approved_case(root, name):
    with fixture_git_policy():
        repository(root)
    git(root, "config", "core.autocrlf", "false")
    git(root, "branch", "-M", "main")
    if name == "boundary":
        (root / "core.py").write_text("STABLE = True\n", encoding="utf-8")
        git(root, "add", "core.py")
        git(root, "commit", "-qm", "core")
    bootstrap_project(root, {"test_commands": [], "max_attempts": 2})
    value = product()
    if name == "manual":
        from tests.test_manual_acceptance import mixed_product
        value = mixed_product()
    identifier = legacy_create_change(root, "orders", value, now=lambda: NOW)["change_id"]
    approve_change(root, identifier, now=lambda: NOW)
    return identifier


def observed_services(identifier, name, calls):
    def generate(project, context):
        calls.append(["acceptance", context.get("repair") is not None])
        return smoke_payload(identifier)

    def implement(project, context):
        calls.append(["implement", context["attempt"]])
        if name == "boundary":
            (project / "core.py").write_text("STABLE = False\n", encoding="utf-8")
        elif name != "success" or context["attempt"] == 2:
            target = project / "modules/orders/service.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("VALUE = 1\n", encoding="utf-8")
        return {"summary": "done", "questions": []}

    def review(project, context):
        calls.append(["review", context["attempt"]])
        if name == "cost":
            from storyloop.cost_limits import check_before_call
            check_before_call(project, identifier, load_project_config(project))
        return {"verdict": "pass", "summary": "ok", "findings": [], "questions": []}

    return LoopServices(generate, implement, review)


def execute_case(root, name, calls):
    if name == "routed":
        return routed_run(root, calls)
    if name == "supersede":
        return superseded_run(root)
    identifier = approved_case(root, name)
    services = observed_services(identifier, name, calls)
    if name == "repair":
        return repaired_run(root, identifier, services, calls)
    if name == "cost":
        update_config(root, limits={"max_calls": 0})
    first = run_change(root, identifier, services)
    results = [first]
    if name == "manual":
        from storyloop.manual_acceptance import record_manual_acceptance
        results.append(record_manual_acceptance(root, identifier, ["AC-2"], "layout checked", now=lambda: NOW))
    elif name == "cost":
        update_config(root, limits={"max_calls": 1})
    if name in {"manual", "cost"}:
        results.append(run_change(root, identifier, services))
    return identifier, results


def repaired_run(root, identifier, services, calls):
    from storyloop.acceptance_repair import repair_smoke
    from tests.test_smoke_repair import defective_payload
    update_config(root, max_attempts=1)

    def defective(project, context):
        calls.append(["acceptance", False])
        return defective_payload(identifier)

    first = run_change(root, identifier, replace(services, generate_smoke=defective))
    repair = repair_smoke(root, identifier, "repair the assertion", services, now=lambda: NOW)
    return identifier, [first, repair, run_change(root, identifier, services)]


def superseded_run(root):
    from storyloop.supersede import supersede_change
    from tests.test_supersede import contract_stop, revised
    identifier = contract_stop(root)
    result = supersede_change(root, identifier, "revise AC-1", revised)
    return identifier, [result]


def routed_run(root, calls):
    from storyloop.design_approval import approve_design
    change, services = routed_case(root, "heavy")
    identifier = change["change_id"]

    def observe(role, function):
        def call(project, context):
            calls.append([role, context.get("mode")])
            return function(project, context)
        return call

    services = replace(services, **{role: observe(role, getattr(services, role))
                                   for role in ("implement", "design", "review", "characterize")})
    first = run_change(root, identifier, services)
    approved = approve_design(root, identifier, first["plan_id"])
    return identifier, [first, approved, run_change(root, identifier, services)]


def update_config(root, **changes):
    config = load_project_config(root)
    config.update(changes)
    write_json(root / ".storyloop/config.json", config)
