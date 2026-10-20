"""OpenSpec-format artifacts for a change: delta spec, living spec merge, decisions record.

Formats follow the OpenSpec CLI (Fission-AI) so `openspec validate` and `openspec list --specs`
accept them: a change carries `specs/<capability>/spec.md` with requirements grouped under
`## ADDED Requirements` / `## MODIFIED Requirements`, each `### Requirement: <name>` holding
SHALL statements and `#### Scenario:` blocks; the living spec at `openspec/specs/<capability>/
spec.md` has `# <capability> Specification`, `## Purpose` and `## Requirements`.

StoryLoop merges deltas itself at completion (instead of `openspec archive`) because the
change directory must stay in place: it is the protected contract and the traceability anchor.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess

from .models import StoryLoopError, identifier, require
from .storage import load_json

REQUIREMENT = re.compile(r"^### Requirement: (.+?)\s*$", re.M)
OWNER = re.compile(r"^_Story (\S+?)(?:, priority \S+)?, from (\S+?)_\s*$", re.M)
SECTION = re.compile(r"^## (ADDED|MODIFIED|REMOVED|RENAMED) Requirements\s*$", re.M)


def capability_of(product: dict, change_id: str) -> str:
    return product.get("capability") or change_id.lower()


def render_delta_spec(product: dict, change_id: str, main_spec: str | None) -> str:
    """Render the change's requirements as an OpenSpec delta against the current living spec."""
    added, modified = [], []
    for story in product["stories"]:
        block = _requirement_block(story, change_id)
        previous = _block_of(main_spec or "", story["title"])
        if previous is None:
            added.append(block)
        else:
            modified.append(_carry_over(block, previous))
    parts = [f"# {capability_of(product, change_id)} — delta for {change_id}", ""]
    if added:
        parts += ["## ADDED Requirements", "", *added]
    if modified:
        parts += ["## MODIFIED Requirements", "", *modified]
    return "\n".join(parts).rstrip("\n") + "\n"


def merge_into_main_spec(main_spec: str | None, delta: str, capability: str, change_id: str,
                         ) -> tuple[str, list[dict]]:
    """Apply ADDED (append) and MODIFIED (replace) blocks; report which owners were replaced."""
    main = main_spec or (f"# {capability} Specification\n\n## Purpose\n"
                         f"Behavior of the {capability} capability, accumulated from StoryLoop changes.\n\n"
                         "## Requirements\n")
    replaced: list[dict] = []
    for section, blocks in _delta_sections(delta).items():
        for name, block in blocks:
            stamped = block.rstrip("\n") + f"\n\n_Updated by {change_id}_\n" if section == "MODIFIED" else block
            existing = _block_of(main, name)
            if existing is not None:
                owner = OWNER.search(existing)
                if owner and owner.group(2) != change_id:
                    fresh = {n for n, text in _scenarios(block) if CARRIED not in text}
                    replaced.append({"change_id": owner.group(2), "story_id": owner.group(1),
                                     "requirement": name,
                                     "superseded": [n for n, _ in _scenarios(existing) if n in fresh]})
                main = _replace_block(main, name, stamped)
            else:
                main = main.rstrip("\n") + "\n\n" + stamped.rstrip("\n") + "\n"
    return main, replaced


def validate_with_cli(project: Path, kind: str, identifier_: str) -> dict:
    """Run the OpenSpec CLI on one change or spec; skipped when the CLI is not installed."""
    executable = shutil.which("openspec")
    if not executable:
        return {"status": "skipped", "reason": "openspec CLI not installed"}
    command = [executable, "validate", identifier_, "--json"] if kind == "change" else \
        [executable, "spec", "validate", identifier_, "--json"]
    completed = subprocess.run(command, cwd=project, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
    try:
        payload = json.loads(completed.stdout[completed.stdout.index("{"):])
    except (ValueError, IndexError):
        raise StoryLoopError(f"openspec validate produced no JSON (exit {completed.returncode}): "
                             f"{completed.stderr[-500:]}") from None
    items = payload.get("items") or [payload]
    problems = []
    for item in items:
        if item.get("valid") is False:
            for issue in item.get("issues") or []:
                problems.append(f"{item.get('id', identifier_)}: {issue.get('message') or issue}")
            if not item.get("issues"):
                problems.append(f"{item.get('id', identifier_)}: invalid")
    if problems:
        raise StoryLoopError("OpenSpec validation failed: " + "; ".join(problems[:5]))
    return {"status": "valid", "kind": kind, "id": identifier_}


def render_decisions(change: dict, answers: list[dict], manual: list[dict], resets: list[dict],
                     repairs: list[dict] | None = None, overrules: list[dict] | None = None) -> str:
    """Every human input that shaped the change, in order, for later readers and reviewers."""
    lines = [f"# Decisions for {change['change_id']}", "", "## Idea", "", change.get("idea", "") or "-", ""]
    link = change.get("supersedes")
    if link:
        lines += [f"## Revision of {link['change_id']}", "",
                  f"- Problem: {link.get('blocker') or '-'}",
                  f"- Requested change: {link.get('message') or '-'}",
                  f"- Why it is a contract defect: {link.get('reason') or 'NEEDS_SPEC_FIX'}", ""]
    lines += ["## Product revisions", ""]
    history = change.get("history") or []
    lines += [f"- r{item.get('revision')} ({item.get('replaced_at', '')}): {item.get('message') or '(no message)'}"
              for item in history] or ["- none"]
    lines += ["", "## Clarifications", ""]
    clarifications = (change.get("product") or {}).get("clarifications") or []
    lines += [f"- [{item['session']}] {item['question']} → {item['answer']}" for item in clarifications] or ["- none"]
    lines += ["", "## Answers to agent questions", ""]
    lines += [f"- attempt {item.get('attempt')} ({item.get('answered_at', '')}): {item['question']} → {item['answer']}"
              for item in answers] or ["- none"]
    lines += ["", "## Manual acceptance", ""]
    lines += [f"- {item['criterion_id']} ({item.get('accepted_at', '')}): {item.get('note', '')}"
              for item in manual] or ["- none"]
    lines += ["", "## Resets", ""]
    lines += [f"- {item.get('at', '')}: from {item.get('from_status')} at attempt {item.get('attempt')} — {item.get('reason')}"
              for item in resets] or ["- none"]
    lines += ["", "## Smoke suite repairs", ""]
    lines += [f"- {item.get('at', '')} ({str(item.get('commit', ''))[:9]}): {item.get('reason')}"
              for item in (repairs or [])] or ["- none"]
    lines += ["", "## Overruled attributions", ""]
    lines += [f"- {item.get('at', '')} {item.get('status')} at attempt {item.get('attempt')}: {item.get('reason')} "
              f"({', '.join(item.get('findings') or [])})" for item in (overrules or [])] or ["- none"]
    return "\n".join(lines) + "\n"


def _requirement_block(story: dict, change_id: str) -> str:
    lines = [f"### Requirement: {story['title']}", ""]
    meta = f"_Story {story['id']}"
    if story.get("priority"):
        meta += f", priority {story['priority']}"
    lines.append(meta + f", from {change_id}_")
    lines.append("")
    lines.append(f"As {story['as_a']}, {story['want']}, so that {story['so_that']}.")
    lines.append("")
    for rule in story["rules"]:
        lines.append(rule if "SHALL" in rule.upper() else f"The system SHALL {rule}")
    lines.append("")
    for criterion in story["acceptance"]:
        # The scenario name is the criterion id: stable across revisions, so OpenSpec can match it.
        lines.append(f"#### Scenario: {criterion['id']}")
        lines.append(f"- **GIVEN** {criterion['given']}")
        lines.append(f"- **WHEN** {criterion['when']}")
        lines.append(f"- **THEN** {criterion['then']}")
        if criterion.get("kind") == "manual":
            lines.append("- _manual acceptance_")
        lines.append("")
    return "\n".join(lines) + "\n"


SCENARIO = re.compile(r"^#### Scenario: (.+?)\s*$", re.M)
CARRIED = "_carried over unchanged from "


def _scenarios(block: str) -> list[tuple[str, str]]:
    matches = list(SCENARIO.finditer(block))
    result = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(block)
        result.append((match.group(1), block[match.start():end].rstrip("\n") + "\n"))
    return result


def _carry_over(block: str, previous: str) -> str:
    """OpenSpec refuses a MODIFIED block that drops scenarios: keep the old ones, marked."""
    names = {name for name, _ in _scenarios(block)}
    owner = OWNER.search(previous)
    origin = owner.group(2) if owner else "an earlier change"
    carried = [text for name, text in _scenarios(previous) if name not in names]
    if not carried:
        return block
    extra = "\n".join(text.rstrip("\n") + (f"\n- {CARRIED}{origin}_\n" if CARRIED not in text else "\n")
                       for text in carried)
    return block.rstrip("\n") + "\n\n" + extra + "\n"


def _delta_sections(delta: str) -> dict[str, list[tuple[str, str]]]:
    sections: dict[str, list[tuple[str, str]]] = {}
    matches = list(SECTION.finditer(delta))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(delta)
        sections[match.group(1)] = _blocks(delta[match.end():end])
    return sections


def _blocks(text: str) -> list[tuple[str, str]]:
    matches = list(REQUIREMENT.finditer(text))
    result = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result.append((match.group(1), text[match.start():end].strip("\n") + "\n"))
    return result


def _block_of(main: str, name: str) -> str | None:
    matches = list(REQUIREMENT.finditer(main))
    for index, match in enumerate(matches):
        if match.group(1) == name:
            end = matches[index + 1].start() if index + 1 < len(matches) else len(main)
            return main[match.start():end]
    return None


def _replace_block(main: str, name: str, block: str) -> str:
    matches = list(REQUIREMENT.finditer(main))
    for index, match in enumerate(matches):
        if match.group(1) == name:
            end = matches[index + 1].start() if index + 1 < len(matches) else len(main)
            return main[:match.start()] + block.rstrip("\n") + "\n\n" + main[end:].lstrip("\n")
    return main


def document_change(project: Path, change_id: str, design_path: Path | None = None,
                    capability: str | None = None) -> dict:
    """Regenerate the OpenSpec artifacts of a finished change from recorded state (no agent call).

    Used to backfill changes completed before these artifacts existed, or to attach a design
    written outside the loop. Files are written, not committed: commit them through the base
    flow (`base-propose` + `base-update`) so the baseline head advances with them.
    """
    project = Path(project).resolve()
    change_id = identifier(change_id, "change id")
    change = load_json(project / ".storyloop/state/changes" / f"{change_id}.json")
    run_path = project / ".storyloop/state/runs" / f"{change_id}.json"
    require(change.get("status") == "PASSED" and run_path.exists(),
            "Only a PASSED change can be documented after the fact")
    state = load_json(run_path)
    answers_path = project / ".storyloop/state/answers" / f"{change_id}.json"
    answers = load_json(answers_path)["entries"] if answers_path.exists() else []
    manual = state.get("manual_acceptance") or []
    base = project / "openspec" / "changes" / change_id
    require(base.is_dir(), "The change's OpenSpec directory is missing")
    written: list[str] = []
    if design_path is not None:
        design = Path(design_path).read_text(encoding="utf-8")
        (base / "design.md").write_text(design.rstrip("\n") + "\n", encoding="utf-8")
        written.append(f"openspec/changes/{change_id}/design.md")
    (base / "decisions.md").write_text(
        render_decisions(change, answers, manual, state.get("resets") or [], state.get("repairs") or [],
                         overrules=state.get("overrules") or []),
        encoding="utf-8")
    written.append(f"openspec/changes/{change_id}/decisions.md")
    if capability:
        require("capability" not in change["product"], "The contract already names its capability")
        capability = identifier(capability, "capability")
    else:
        capability = capability_of(change["product"], change_id)
    delta_path = base / "specs" / capability / "spec.md"
    main_path = project / "openspec" / "specs" / capability / "spec.md"
    current = main_path.read_text(encoding="utf-8") if main_path.is_file() else None
    if not delta_path.is_file():
        delta_path.parent.mkdir(parents=True, exist_ok=True)
        delta_path.write_text(render_delta_spec(change["product"], change_id, current), encoding="utf-8")
        written.append(f"openspec/changes/{change_id}/specs/{capability}/spec.md")
    main_path.parent.mkdir(parents=True, exist_ok=True)
    merged, _ = merge_into_main_spec(current, delta_path.read_text(encoding="utf-8"), capability, change_id)
    main_path.write_text(merged, encoding="utf-8")
    written.append(f"openspec/specs/{capability}/spec.md")
    return {"status": "DOCUMENTED", "change_id": change_id, "written": written,
            "next": "storyloop base-propose <project> --path ... --reason \"documentation\" then commit and base-update"}
