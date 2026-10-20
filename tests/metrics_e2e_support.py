"""Offline subprocess scenarios; production APIs alone write engine evidence."""
from copy import deepcopy
from pathlib import Path
import sys

from storyloop.approvals import approve_change
from storyloop.baseline import load_project_config
from storyloop.changes import create_change, revise_change
from storyloop.design_approval import approve_continue_heavy, approve_design
from storyloop.models import StoryLoopError
from storyloop.project_loop import load_run_state, run_change
from storyloop.storage import write_json
from storyloop.supersede import supersede_change
from tests.lesson_support import write_lesson
from tests.routing_support import routed_acceptance, routed_case, routed_product
from tests.test_baseline_behavior import git
from tests.test_change_nature import fix_product
from tests.test_lessons import sample
from tests.test_roles_e2e import finding
from tests.test_routing_planning import answer


def product_for(name, origin=None):
    value = routed_product()
    value["assessment"]["scope"]["creates"] = [f"modules/orders/{name}.py"]
    if origin:
        value["nature"] = "fix"
        value["defect"] = deepcopy(fix_product()["defect"])
        value["defect"].update(
            observed="Missing version marker", expected="Version marker equals one",
            reproduction=["Read the missing version marker"], origin_change=origin)
        value["stories"][0]["acceptance"][0]["reproduces_defect"] = True
        value["coverage_exemptions"] = deepcopy(fix_product()["coverage_exemptions"])
    return value


def implementation(name, value=1, **extra):
    return {"role": "implement", "result": {
        "summary": "Write version marker", "questions": [], **extra},
        "creates": [{"path": f"modules/orders/{name}.py",
                     "content": f"VALUE = {value}\n"}]}


def review(findings=None):
    return {"verdict": "fail" if findings else "pass", "summary": "Checked version",
            "findings": findings or [], "questions": [], "coverage": ["implementation"],
            "checklist": []}


def install(root, identifier, name, product, calls=None, split=False, budget=6):
    adapter = Path(__file__).resolve().parents[1] / "storyloop/testing/mock_adapter.py"
    folder = root.parent / identifier
    folder.mkdir()
    acceptance = routed_acceptance(identifier)
    acceptance["files"][0]["content"] = acceptance["files"][0]["content"].replace(
        "modules/orders/service.py", f"modules/orders/{name}.py")
    design = answer(product)
    if split:
        design["options"][0]["creates"] += [f"app/extra{i}.py" for i in range(15)]
    lock_path = f"tests/test_lock_{name}.py"
    lock_source = ("import unittest\nfrom app.service import serve\n"
                   "class Lock(unittest.TestCase):\n"
                   "    def test_existing(self): self.assertEqual(serve(), 'ok')\n")
    defaults = [
        {"role": "acceptance", "result": acceptance},
        {"role": "design", "result": design},
        {"role": "characterize", "result": {"summary": "Lock existing service",
         "touched": ["app/service.py"], "tests": [lock_path], "questions": []},
         "creates": [{"path": lock_path, "content": lock_source}]},
        {"role": "review", "mode": "retrospective", "result": {
            "lessons": [], "errata": [], "uncovered_concepts": [],
            "nothing_to_learn": "Scenario has no reusable addition"}},
        {"role": "review", "mode": "consistency", "result": {
            "verdict": "coherent", "summary": "Contract, oracle and plan agree",
            "questions": [], "findings": []}},
        {"role": "review", "result": review()}, implementation(name)]
    script_calls = (calls or []) + defaults
    for call in script_calls:
        call["telemetry"] = {"model": "fixture-model", "vendor": "fixture",
                             "tier_applied": "strong", "cost_usd": 0.001}
    script = folder / "script.json"
    write_json(script, {"capabilities": {"vendor": "fixture", "tiers": ["strong", "standard"],
        "subagents": False, "path_guard": False, "fast_checks": False, "modes": ["*"]},
        "calls": script_calls})
    config = load_project_config(root)
    config.update(adapter="command", agent_command=[sys.executable, str(adapter),
                  "--script", str(script)], max_attempts=budget)
    write_json(root / ".storyloop/config.json", config)


def finish(root, identifier, case, expected="PASSED", **kwargs):
    if expected == "AGENT_UNAVAILABLE":
        with case.assertRaises(StoryLoopError) as caught:
            run_change(root, identifier, **kwargs)
        case.assertEqual(caught.exception.code, expected)
        return None
    result = run_change(root, identifier, **kwargs)
    # Real design consents are required both before locks and after a changed binding.
    for _ in range(4):
        if result["status"] != "DESIGN_APPROVAL_REQUIRED":
            break
        approve_design(root, identifier, result["plan_id"])
        result = run_change(root, identifier)
    case.assertEqual(result["status"], expected, result)
    return result


def leave_candidate(root, identifier):
    state = load_run_state(root, identifier)
    # Keep stopped work on its feature branch; never rewrite a run or discard evidence.
    if git(root, "status", "--porcelain").strip():
        git(root, "stash", "push", "-u", "-m", f"Retain {identifier} candidate")
    git(root, "checkout", state["source_branch"])


def new_change(root, name, case, calls=None, tier="light", expected="PASSED",
               origin=None, split=False, budget=6):
    value = product_for(name, origin)
    change = create_change(root, name, value)
    identifier = change["change_id"]
    approve_change(root, identifier, tier=None if tier == "light" else tier)
    install(root, identifier, name, value, calls, split, budget)
    if split:
        result = run_change(root, identifier)
        case.assertEqual(result["status"], "SPLIT_RECOMMENDED", result)
        assessment = load_run_state(root, identifier)["planning"]["assessment"]["id"]
        approve_continue_heavy(root, identifier, assessment, "Exercise atomic delivery")
    finish(root, identifier, case, expected)
    return identifier


def contract_review(name, nth=None):
    item = finding(95, "The requested version conflicts with the contract")
    item.update(target="contract", criterion_id="AC-1", location=f"modules/orders/{name}.py:1")
    return {"role": "review", "mode": None, "result": review([item]),
            **({"nth": nth} if nth else {})}


def supersession(root, case):
    old = new_change(root, "spec", case, [contract_review("spec")],
                     tier="standard", expected="NEEDS_SPEC_FIX")
    new = supersede_change(root, old, "Clarify version one",
                           lambda context: product_for("spec"), reason="Resolve contradiction")
    leave_candidate(root, old)
    identifier = new["change_id"]
    revise_change(root, identifier, product_for("spec"), "Reassess source branch")
    approve_change(root, identifier, tier="standard")
    install(root, identifier, "spec", product_for("spec"))
    finish(root, identifier, case)
    case.assertEqual(load_run_state(root, old)["status"], "SUPERSEDED")
    return old, identifier


def continued_and_overruled(root, case, changes):
    changes["heavy"] = new_change(root, "heavy", case, tier="heavy", split=True)
    identifier = new_change(root, "overrule", case, [contract_review("overrule", 1)],
                            tier="standard", expected="NEEDS_SPEC_FIX")
    finish(root, identifier, case, overrule="Confirmed version one is the intended contract")
    changes["overrule"] = identifier


def ci_records(root, changes):
    folder = root.parent / "ci-results"
    folder.mkdir()
    for name, status in (("first", "FAILED"), ("second", "PASSED")):
        state = load_run_state(root, changes[name])
        write_json(folder / f"ci-{name}.json", {
            "version": 1, "change_id": changes[name], "status": status,
            "commit": state["merge_commit"], "recorded_at": "2026-10-20T12:00:00Z",
            "layers": {"acceptance": status, "smoke": "PASSED"}})
    return folder


def run_scenarios(directory, case):
    root = directory / "project"
    root.mkdir()
    initial, _ = routed_case(root, "light")
    changes = {"first": initial["change_id"]}
    install(root, changes["first"], "service", routed_product())
    finish(root, changes["first"], case)
    changes["second"] = new_change(root, "second", case)
    changes["fix"] = new_change(root, "fix", case, origin=changes["first"])
    lesson = sample(baseline_commit=git(root, "rev-parse", "HEAD"), tier="core")
    write_lesson(root, lesson)
    git(root, "add", "openspec/lessons")
    git(root, "commit", "-qm", "Add version lesson")
    bad = implementation("multi", 0)
    bad["nth"] = 1
    changes["multi"] = new_change(root, "multi", case, [bad])
    changes["budget"] = new_change(root, "budget", case, [implementation("budget", 0)],
                                  expected="BUDGET_EXHAUSTED", budget=1)
    leave_candidate(root, changes["budget"])
    risk = implementation("upgrade", routing_risks=[
        {"risk": "migration", "statement": "Version storage changes",
         "location": "modules/orders/upgrade.py:1"}])
    risk["nth"] = 1
    changes["upgrade"] = new_change(root, "upgrade", case, [risk], tier="standard")
    changes["spec"], changes["superseding"] = supersession(root, case)
    changes["stuck"] = new_change(root, "stuck", case, [implementation("stuck", 0)],
                                 expected="NO_PROGRESS")
    leave_candidate(root, changes["stuck"])
    changes["agent"] = new_change(root, "agent", case,
        [{"role": "implement", "nth": 1, "exit": 3, "stdout": "adapter unavailable"}],
        expected="AGENT_UNAVAILABLE")
    finish(root, changes["agent"], case)
    continued_and_overruled(root, case, changes)
    return root, changes, ci_records(root, changes)
