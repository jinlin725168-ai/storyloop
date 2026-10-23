"""OpenSpec-shaped product change drafts and revisions."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Callable

from .baseline import load_baseline, load_project_config
from .models import identifier, nature_of, require, text, validate_product
from .openspec_docs import capability_of, render_delta_spec
from .requirement_checklist import evaluate_checklist, render_checklist
from .storage import load_json, write_json, write_text_atomic

Clock = Callable[[], datetime]


def create_change(
    project: Path, idea: str, product: dict, now: Clock | None = None,
    supersedes: dict | None = None,
) -> dict:
    """Create a local ignored draft from one short product idea."""
    project = Path(project).resolve()
    load_baseline(project)
    if (config := load_project_config(project)).get("adapters") or config.get("roles"):
        from .adapter_capabilities import ensure_checked
        ensure_checked(project)
    idea = text(idea, "product idea")
    canonical = _validated_copy(product)
    moment = (now or _utc_now)()
    change_id = _next_id(project, moment, nature_of(canonical))
    timestamp = _timestamp(moment)
    state = {
        "version": 1,
        "routing_version": 1,
        "change_id": change_id,
        "status": "DRAFT",
        "revision": 1,
        "idea": idea,
        "product": canonical,
        "created_at": timestamp,
        "updated_at": timestamp,
        "history": [],
        "approval": None,
    }
    if supersedes is not None:
        state["supersedes"] = _supersedes(supersedes)
    from .intake import project_intake
    state["intake"] = project_intake(project, canonical)
    _save(project, state)
    return deepcopy(state)


def load_change(project: Path, change_id: str) -> dict:
    """Load a validated local product change state."""
    change_id = identifier(change_id, "change id")
    value = load_json(_state_path(Path(project).resolve(), change_id))
    required = {
        "version", "change_id", "status", "revision", "idea", "product",
        "created_at", "updated_at", "history", "approval",
    }
    from .models import require
    optional = {"supersedes", "superseded_by", "routing_version", "intake"}
    require(required <= set(value) <= required | optional and value["version"] == 1,
            "Invalid change state")
    require(value["change_id"] == change_id, "Change state identity mismatch")
    require(value["status"] in {
        "DRAFT", "APPROVED", "RUNNING", "PASSED", "PAUSED", "FAILED", "SUPERSEDED",
    },
            "Invalid change status")
    require(type(value["revision"]) is int and value["revision"] > 0, "Invalid change revision")
    if "routing_version" in value:
        require(type(value["routing_version"]) is int and value["routing_version"] == 1,
                "Invalid routing version")
        from .intake import validate_intake_record
        validate_intake_record(value.get("intake"))
    text(value["idea"], "product idea")
    validate_product(value["product"])
    require(isinstance(value["history"], list), "Invalid change history")
    if "supersedes" in value:
        _supersedes(value["supersedes"])
    if "superseded_by" in value:
        identifier(value["superseded_by"], "superseding change id")
    return value


def _supersedes(value: object) -> dict:
    """Validate and copy the immutable link to the run whose contract was replaced."""
    require(isinstance(value, dict), "Invalid supersedes link")
    keys = {"change_id", "message", "reason", "blocker", "source_branch", "design_summary"}
    require(set(value) == keys, "Invalid supersedes link")
    result = deepcopy(value)
    identifier(result["change_id"], "superseded change id")
    text(result["message"], "revision message")
    text(result["source_branch"], "source branch")
    for key in ("reason", "blocker", "design_summary"):
        require(result[key] is None or isinstance(result[key], str), "Invalid supersedes link")
    return result


def revise_change(
    project: Path, change_id: str, product: dict, message: str = "",
    now: Clock | None = None, clarifications: list[dict] | None = None,
) -> dict:
    """Replace a draft only after validating the complete next product contract.

    `clarifications` are human answers given in this revision; they are appended to the
    contract's clarification record (never dropped by later revisions) and dated by session.
    """
    project = Path(project).resolve()
    current = load_change(project, change_id)
    run_path = project / ".storyloop" / "state" / "runs" / f"{change_id}.json"
    require(not run_path.exists(),
            "A change cannot be revised after execution starts; create a new product change")
    canonical = _validated_copy(product)
    moment = (now or _utc_now)()
    canonical["clarifications"] = _merge_clarifications(
        current["product"].get("clarifications", []), canonical.get("clarifications", []),
        clarifications or [], moment.strftime("%Y-%m-%d"))
    timestamp = _timestamp(moment)
    prior = {
        "revision": current["revision"],
        "digest": contract_digest(current["product"]),
        "message": message.strip(),
        "replaced_at": timestamp,
    }
    updated = {
        **current,
        "routing_version": 1,
        "status": "DRAFT",
        "revision": current["revision"] + 1,
        "product": canonical,
        "updated_at": timestamp,
        "history": [*current["history"], prior],
        "approval": None,
    }
    from .intake import project_intake
    updated["intake"] = project_intake(project, canonical)
    _save(project, updated)
    return deepcopy(updated)


def _merge_clarifications(previous: list[dict], returned: list[dict], answered: list[dict],
                          session: str) -> list[dict]:
    """Keep every earlier Q→A, add what the agent preserved and what the human just answered."""
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in [*previous, *returned]:
        key = (item["question"], item["answer"])
        if key not in seen:
            seen.add(key)
            merged.append(dict(item))
    for item in answered:
        question, answer = text(item.get("question"), "question"), text(item.get("answer"), "answer")
        if (question, answer) not in seen:
            seen.add((question, answer))
            merged.append({"session": session, "question": question, "answer": answer})
    return merged


def contract_digest(product: dict) -> str:
    """Fingerprint only the user-facing product contract."""
    canonical = json.dumps(product, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def draft_directory(project: Path, change_id: str) -> Path:
    return Path(project).resolve() / ".storyloop" / "drafts" / identifier(change_id, "change id")


def _save(project: Path, state: dict) -> None:
    """Persist state without replacing the evidence of an existing contract."""
    write_json(_state_path(project, state["change_id"]), state, max_bytes=2_000_000)
    write_change_preview(project, state)


def write_change_preview(project: Path, state: dict) -> None:
    """Regenerate every preview byte from the canonical product state."""
    root = draft_directory(project, state["change_id"])
    product = state["product"]
    stories = []
    criteria = []
    links = []
    for story in product["stories"]:
        stories.append({key: deepcopy(value) for key, value in story.items() if key != "acceptance"})
        for criterion in story["acceptance"]:
            criteria.append({"story_id": story["id"], **deepcopy(criterion)})
            links.append({"criterion_id": criterion["id"], "story_id": story["id"], "tests": []})
    write_json(root / "stories.json", {"version": 1, "stories": stories}, max_bytes=1_000_000)
    write_json(root / "acceptance.json", {"version": 1, "criteria": criteria}, max_bytes=1_000_000)
    write_json(root / "traceability.json", {"version": 1, "criteria": links}, max_bytes=1_000_000)
    non_goals = "\n".join(f"- {item}" for item in product["non_goals"]) or "- 无"
    questions = "\n".join(_question_line(item) for item in product["questions"]) or "- 无"
    story_lines = "\n".join(
        f"- {story['id']} [{story.get('priority', '-')}] {story['title']}"
        + (f"（独立验证：{story['independent_test']}）" if story.get("independent_test") else "")
        for story in product["stories"]) or "- 无"
    capability = product.get("capability")
    proposal = (
        f"# {state['change_id']}\n\n"
        + (f"能力：`{capability}`\n\n" if capability else "")
        + f"## 产品目标\n\n{product['goal']}\n\n"
        f"## 非目标\n\n{non_goals}\n\n"
        f"## 用户故事\n\n{story_lines}\n\n"
        f"## 澄清记录\n\n{_clarification_lines(product.get('clarifications', []))}\n\n"
        f"## 待决问题\n\n{questions}\n"
    )
    _write_text(root / "proposal.md", proposal)
    capability = capability_of(product, state["change_id"])
    main_path = Path(project).resolve() / "openspec" / "specs" / capability / "spec.md"
    main_spec = main_path.read_text(encoding="utf-8") if main_path.is_file() else None
    _write_text(root / "specs" / capability / "spec.md",
                render_delta_spec(product, state["change_id"], main_spec))
    _write_text(root / "checklist.md", render_checklist(evaluate_checklist(product, project)))


def _question_line(item: dict) -> str:
    options = " / ".join(
        f"{option}（推荐）" if option == item.get("recommended") else option for option in item["options"])
    why = f" 为什么重要：{item['why']}" if item.get("why") else ""
    return f"- {item['id']}: {item['text']} 选项：{options}{why}"


def _clarification_lines(entries: list[dict]) -> str:
    if not entries:
        return "- 无"
    lines = []
    session = None
    for item in entries:
        if item["session"] != session:
            session = item["session"]
            lines.append(f"### Session {session}")
        lines.append(f"- {item['question']} → {item['answer']}")
    return "\n".join(lines)


def _write_text(path: Path, content: str) -> None:
    write_text_atomic(path, content)


def _next_id(project: Path, moment: datetime, nature: str = "feature") -> str:
    from .change_types import definition_for
    prefix = definition_for(nature).id_prefix + "-" + moment.astimezone(timezone.utc).strftime("%Y%m%d") + "-"
    root = project / ".storyloop" / "state" / "changes"
    existing = {path.stem for path in root.glob(prefix + "*.json")} if root.is_dir() else set()
    for number in range(1, 1000):
        candidate = prefix + f"{number:03d}"
        if candidate not in existing:
            return candidate
    from .models import StoryLoopError
    raise StoryLoopError("Daily change ID capacity exhausted")


def _state_path(project: Path, change_id: str) -> Path:
    return project / ".storyloop" / "state" / "changes" / f"{change_id}.json"


def _validated_copy(product: dict) -> dict:
    value = deepcopy(product)
    validate_product(value)
    return value


def _timestamp(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
