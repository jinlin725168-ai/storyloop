"""The two gates between RED and the first implementation attempt: design and characterization.

design (the architect) runs once per change and names the existing files the change will touch;
characterization locks the current behaviour of those files with tests that must be green on the
old code before anyone changes it.
"""

from __future__ import annotations

from .errata import defer_errata, release_errata

from pathlib import Path
from typing import TYPE_CHECKING

from .planning import plan_projection, required_locks
from .routing_restore import restore_lock_paths
from .characterization_locks import (capture_locks, routed_coverage, require_saved_coverage, verified_locks)
from .answers import load_answers
from .baseline import _git
from .facts import merge_facts
from .models import StoryLoopError, nature_of, require
from .project_boundaries import audit_project_changes
from .project_git import commit_paths
from .project_loop_guards import exists_at, read_only_call, require_git_visible, write_safely
from .project_loop_support import run_path, save_run
from .run_stops import pause, pause_for_boundary
from .changes import load_change
from .oracle_counts import removal_paths, sample_layer
from .project_profile import layer_commands, matches_test_root
from .characterization_baseline import run_characterization_baseline
from .storage import load_json

if TYPE_CHECKING:  # the loop imports this module; the services type is only an annotation
    from .run_services import LoopServices


def design_plan(state: dict) -> dict | None:
    if state.get("routing", {}).get("version") == 1:
        return plan_projection(state)
    plan = state.get("design_plan") or {}
    if plan.get("status") != "DONE":
        return None
    return {"summary": plan["summary"], "touched": list(plan["touched"]), "design": plan["design"]}


def check_design_answer(project: Path, state: dict, response: object) -> None:
    """Check the architect's result without changing run or repository state."""
    require(isinstance(response, dict) and isinstance(response.get("summary"), str)
            and isinstance(response.get("touched"), list)
            and all(isinstance(path, str) for path in response["touched"])
            and isinstance(response.get("design"), str)
            and isinstance(response.get("questions"), list)
            and all(isinstance(question, str) and question.strip()
                    for question in response["questions"]),
            "Design agent returned an invalid result")
    if response["questions"]:
        return
    require(response["design"].strip(), "Design must contain the plan as Markdown")
    for path in response["touched"]:
        require(exists_at(project, state["contract_commit"], path),
                f"Design names a file that does not exist at the contract commit: {path}")


def ensure_design(project: Path, change: dict, state: dict, services: LoopServices,
                  config: dict) -> dict | None:
    """The architect step, once per change after RED: a read-only plan naming the files to modify.

    Its `touched` list seeds the characterization gate and its Markdown is what implement follows
    and review checks against. Design questions pause the run like any other agent question.
    Small changes skip it: fewer stories than `design_min_stories` (default 2) means the
    characterize and implement roles work from the contract alone.
    """
    current = state.get("design_plan") or {}
    if current.get("status") in {"DONE", "SKIPPED"}:
        return None
    reason = None
    if services.design is None:
        reason = "no design service"
    else:
        minimum = config.get("design_min_stories", 2)
        require(type(minimum) is int and minimum >= 1, "design_min_stories must be a positive integer")
        stories = len(change["product"]["stories"])
        if stories < minimum:
            reason = (f"small change: {stories} {'story' if stories == 1 else 'stories'} "
                      f"< design_min_stories {minimum}")
    if reason:
        state["design_plan"] = {"status": "SKIPPED", "reason": reason, "touched": [], "design": ""}
        save_run(project, state)
        return None
    change_id = change["change_id"]
    context = {"change": change, "answers": load_answers(project, change_id),
               "facts": list(state.get("facts") or [])}
    _add_superseded_context(project, change, context)
    from .semantic_repair import answer_with_repair
    response = answer_with_repair(
        lambda request: read_only_call(
            project, change_id, lambda: services.design(project, request)),
        context, lambda answer: check_design_answer(project, state, answer))
    merge_facts(project, state, response.get("facts"), "design")
    if response["questions"]:
        return pause(project, state, "NEEDS_INPUT", questions=response["questions"])
    touched = sorted(set(response["touched"]))
    state["design_plan"] = {"status": "DONE", "summary": response["summary"], "touched": touched,
                            "design": response["design"].rstrip("\n") + "\n"}
    state.pop("questions", None)
    save_run(project, state)
    return None


def _add_superseded_context(project: Path, change: dict, context: dict) -> None:
    """Supply old-run material as attributed reference, not as facts verified in this run."""
    link = change.get("supersedes") or {}
    old_id = link.get("change_id")
    if not old_id:
        return
    summary = link.get("design_summary")
    if summary:
        context["prior_design"] = {"from": old_id, "summary": summary}
    path = run_path(project, old_id)
    if path.exists():
        facts = load_json(path).get("facts") or []
        if facts:
            context["prior_facts"] = {"from": old_id, "facts": facts}


def ensure_characterization(project: Path, change: dict, state: dict, services: LoopServices,
                            config: dict) -> dict | None:
    """Layer 3 gate, once per change: lock the current behaviour of the paths about to change."""
    current = state.get("characterization") or {}
    routed = state.get("routing", {}).get("version") == 1
    if current.get("status") == "GREEN" or (current.get("status") == "SKIPPED" and not routed):
        if state.get('routing', {}).get('version') == 1:
            required = _mandatory_locks(change, state, config)
            missing = sorted(set(required) - set(verified_locks(project, state, config)))
            if missing and state.get('planning', {}).get('origin') == 'L3':
                require(services.characterize is not None,
                        'L3 requires a characterization service for missing locks')
                return characterize_paths(project, change, state, services, config, extend=missing)
            require_saved_coverage(project, state, config, required)
        return None
    if (state.get('routing', {}).get('version') == 1
            and state['routing']['effective_tier'] != 'heavy'
            and nature_of(change['product']) != 'refactor'
            and not _mandatory_locks(change, state, config)):
        state['characterization'] = {'status': 'DEFERRED', 'touched': [], 'tests': []}
        save_run(project, state)
        return None
    profile = config["profile"]
    reason = None
    if not profile.get("test_roots"):
        reason = "test_roots not configured"
    elif not layer_commands(profile, "unit"):
        reason = "unit_commands not configured"
    elif services.characterize is None:
        reason = "no characterize service"
    if reason:
        if nature_of(change["product"]) == "refactor" or _mandatory_locks(change, state, config):
            # Characterization is a refactor's behaviour oracle; without it nothing proves "unchanged".
            raise StoryLoopError(f"Refactor or heavy plan needs its characterization gate ({reason})")
        state["characterization"] = {"status": "SKIPPED", "reason": reason, "touched": [], "tests": []}
        save_run(project, state)
        return None
    extend = (_mandatory_locks(change, state, config)
              if state.get('planning', {}).get('origin') == 'L3' else None)
    return characterize_paths(project, change, state, services, config, extend=extend)


def _mandatory_locks(change: dict, state: dict, config: dict) -> list[str]:
    plan = plan_projection(state)
    if not plan:
        return []
    required = required_locks(state['routing']['effective_tier'], nature_of(change['product']),
                              [p for p in plan['touched'] if not matches_test_root(
                                  p, config['profile'].get('test_roots', []))])
    return sorted(set(required) | restore_lock_paths(state))


@defer_errata(manual=True)
def characterize_paths(project: Path, change: dict, state: dict, services: LoopServices, config: dict,
                       extend: list[str] | None) -> dict | None:
    change_id = change["change_id"]
    profile = config["profile"]
    previous = state.get("characterization") or {"touched": [], "tests": []}
    plan = (plan_projection(state, locks_only=True, nature=nature_of(change['product']))
            if state.get('routing', {}).get('version') == 1 else design_plan(state))
    if plan and state.get("routing", {}).get("version") == 1:
        plan['touched'] = [p for p in plan['touched']
                           if not matches_test_root(p, profile.get('test_roots', []))]
    context = {"change": change, "test_roots": profile["test_roots"], "extend": list(extend or []),
               "characterization": previous if previous.get("status") == "GREEN" else None,
               "design": plan}
    response, written = write_safely(project, change_id, lambda: services.characterize(project, context))
    require(isinstance(response, dict) and isinstance(response.get("summary"), str)
            and isinstance(response.get("touched"), list) and isinstance(response.get("tests"), list)
            and all(isinstance(p, str) for p in response["touched"] + response["tests"]),
            "Characterization agent returned an invalid result")
    require(not response.get("questions"), "Characterization asks no questions; it locks what exists")
    boundary = audit_project_changes(project, change_id)
    require_git_visible(written, boundary)
    # Judge only what this call wrote: in extend mode the implementation's own edits are already present.
    changed = sorted({item.path for item in written if not item.path.endswith("/")})
    violating = {item["path"] for item in boundary["violations"]}
    outside = [path for path in changed if path in violating or not matches_test_root(path, profile["test_roots"])]
    if outside:
        violations = [{"path": p, "category": "NOT_A_TEST_PATH"} for p in outside]
        return pause(project, state, "BOUNDARY_VIOLATION", violations=violations, base_proposal=None,
                     error="Characterization may only write test files", phase="prepare")
    release_errata()
    coverage = None
    if state.get('routing', {}).get('version') == 1:
        coverage = routed_coverage(project, state, config, response, changed,
                                   list(extend or []) + (plan['touched'] if plan else []))
    touched = (sorted(coverage) if coverage is not None else sorted(
        set(previous.get("touched", [])) | set(response["touched"]) | set(extend or [])
        | set(plan["touched"] if plan else [])))
    for path in touched:
        require(exists_at(project, state["contract_commit"], path) and not matches_test_root(path, profile["test_roots"]),
                f"Characterized path must be an existing non-test file: {path}")
    pending = {"touched": touched, "tests": sorted(set(response["tests"]) | set(changed)), "changed": changed,
               "extend": list(extend or [])}
    if coverage is not None:
        pending['coverage'] = coverage
    return _verify_characterization(project, change_id, state, config, pending)


def _verify_characterization(project: Path, change_id: str, state: dict, config: dict,
                             pending: dict) -> dict | None:
    """The gate itself: the whole unit layer green on the current code, then lock the tests in a commit.

    A red result pauses with the pending lock kept in the run state, so the human can fix the tests
    in place and `run` re-verifies them without asking the agent again.
    """
    profile = config["profile"]
    locks = (capture_locks(project, config, pending)
             if state.get('routing', {}).get('version') == 1 else None)
    unit = run_characterization_baseline(project, layer_commands(profile, "unit"),
                                         config["check_timeout_seconds"], pending)
    if unit["status"] != "PASSED":
        return pause(project, state, "CHARACTERIZATION_FAILED", unit=unit, pending_characterization=pending,
                     error="Characterization tests must be green on the current code before implementation")
    if locks is not None:
        require(locks == capture_locks(project, config, pending), 'Characterization artifacts changed during checks')
    previous = state.get("characterization") or {"touched": [], "tests": []}
    present = _uncommitted(project, pending["changed"])  # a test the human deleted is simply not locked
    commits = list(previous.get("commits", []))
    if present:
        commits.append(commit_paths(project, present, f"test({change_id}): characterize touched paths")["commit"])
    state["characterization"] = {"status": "GREEN",
                                 "touched": sorted(set(previous.get("touched", [])) | set(pending["touched"])),
                                 "tests": sorted(set(previous.get("tests", [])) | set(pending["tests"])),
                                 "commits": commits}
    if locks is not None:
        state['characterization'].update(
            locks=locks, touched=sorted(locks),
            tests=sorted({test for artifacts in locks.values() for test in artifacts}))
    # The locks are committed on top of the contract commit and the implementation is not: HEAD is the
    # old code plus every lock, so its unit count is the floor. The workspace would not do: in extend
    # mode it already carries the implementation's edits, deleted tests included.
    removals = removal_paths(load_change(project, change_id)["product"])
    head = _git(project, "rev-parse", "HEAD").strip()
    state.setdefault("oracle", {}).setdefault("baseline", {})["unit"] = sample_layer(
        project, config, head, "unit", removals)
    for key in ("pending_characterization", "unit", "error", "next"):
        state.pop(key, None)
    save_run(project, state)
    return None


def resume_characterization(project: Path, change: dict, state: dict, config: dict) -> dict | None:
    """Re-verify a red characterization after the human fixed the tests in place (no agent call).

    The fix may only touch test files: any boundary violation still stops the run. A lock made
    while extending an attempt costs that attempt, exactly as when it is green the first time.
    """
    pending = state.get("pending_characterization")
    require(isinstance(pending, dict) and isinstance(pending.get("changed"), list),
            "Paused characterization lacks its pending lock; start a new product change")
    change_id = change["change_id"]
    boundary = audit_project_changes(project, change_id)
    if boundary["violations"]:
        return pause_for_boundary(project, state, boundary, phase="prepare")
    paused = _verify_characterization(project, change_id, state, config, pending)
    if paused is not None:
        return paused
    state["status"] = "IMPLEMENTING"
    if pending.get("extend") and state.get("planning", {}).get("origin") != "L3":
        state["failures"].append({"attempt": state["attempt"],
                                  "characterization": {"status": "FAILED", "uncharacterized": pending["extend"]}})
    save_run(project, state)
    return None


def _uncommitted(project: Path, paths: list[str]) -> list[str]:
    """The given paths that still differ from HEAD (new, modified or deleted after being tracked)."""
    if not paths:
        return []
    status = _git(project, "status", "--porcelain=v1", "--untracked-files=all", "-z", "--", *paths)
    changed = {entry[3:] for entry in filter(None, status.split("\0"))}
    return [path for path in paths if path in changed]


def uncharacterized_paths(project: Path, state: dict, boundary: dict, config: dict) -> list[str]:
    """Pre-existing non-test files the implementation modified without a characterization lock."""
    characterization = state.get("characterization") or {}
    routed = state.get("routing", {}).get("version") == 1
    if characterization.get("status") != "GREEN" and not (
            routed and characterization.get("status") == "DEFERRED"):
        return []
    roots = config["profile"].get("test_roots") or []
    touched = (set(verified_locks(project, state, config)) if routed
               else set(characterization.get("touched", [])))
    return sorted(item["path"] for item in boundary["allowed"]
                  if item["path"] not in touched and not matches_test_root(item["path"], roots)
                  and exists_at(project, state["contract_commit"], item["path"]))
