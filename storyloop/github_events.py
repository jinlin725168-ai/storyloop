"""Roadmap H: GitHub as an event source, polled with the `gh` CLI (no server, no webhook).

`storyloop github sync` turns new events into drafts, never into spent money or decisions:

- an open issue with the configured label becomes an event draft with a suggested `assess`
  command (the assessment's answers and decision stay the person's);
- the newest run of a workflow on the branch failing becomes a "fix CI" event draft;
- an issue comment `/storyloop <CHANGE-ID> <message>` from an allowed author revises that DRAFT
  change, exactly as `storyloop change --change ID --message ...` would (one human touch).

Issue and comment text is stored as data and never executed. An event draft is handled once an
assessment with its exact idea exists, or when a person dismisses it.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shlex
import subprocess

from .baseline import load_project_config
from .models import StoryLoopError, require
from .processutil import redact
from .storage import load_json, write_json

STATE = Path(".storyloop/state/github.json")
DRAFTS = Path(".storyloop/drafts/events")
COMMENT = re.compile(r"^/storyloop\s+([A-Z]+-\d{8}-[0-9A-Za-z-]+)\s+(.+)$", re.S)
MAX_TEXT = 4000


def add_github_arguments(sub) -> None:
    github = sub.add_parser("github", help="GitHub 事件：issue 标签、main 上 CI 变红、/storyloop 评论 → 草稿（gh 轮询）")
    actions = github.add_subparsers(dest="github_command", required=True)
    sync = actions.add_parser("sync", help="拉取新事件并生成事件草稿或修订草案")
    sync.add_argument("project", type=Path)
    sync.add_argument("--repo", help="owner/name；默认取 config 的 github.repo")
    sync.add_argument("--json", action="store_true", help="输出完整 JSON")
    dismiss = actions.add_parser("dismiss", help="驳回一个事件草稿")
    dismiss.add_argument("project", type=Path)
    dismiss.add_argument("--event", required=True)
    dismiss.add_argument("--json", action="store_true", help="输出完整 JSON")


def dispatch_github(args, revise) -> dict:
    project = args.project.resolve()
    if args.github_command == "dismiss":
        return dismiss_event(project, args.event)
    return sync(project, repo=args.repo, revise=revise)


def gh_json(argv: list[str]):
    result = subprocess.run(["gh", *argv], capture_output=True, text=True, encoding="utf-8", timeout=120)
    if result.returncode != 0:
        raise StoryLoopError("gh failed: " + redact(result.stderr.strip())[:500], code="AGENT_UNAVAILABLE")
    return json.loads(result.stdout or "null")


def sync(project: Path, *, repo: str | None = None, gh=gh_json, revise=None, now=None) -> dict:
    """Fetch new events once; idempotent (seen ids are remembered)."""
    project = Path(project).resolve()
    settings = load_project_config(project).get("github") or {}
    repo = repo or settings.get("repo")
    require(isinstance(repo, str) and re.fullmatch(r"[\w.-]+/[\w.-]+", repo or "") is not None,
            "github sync needs --repo owner/name (or config github.repo)")
    state = load_json(project / STATE) if (project / STATE).is_file() else {"version": 1, "seen": []}
    seen = set(state.get("seen") or [])
    moment = (now or (lambda: datetime.now(timezone.utc)))()
    created, revised, ignored = [], [], []
    label = settings.get("label", "storyloop")
    for issue in gh(["issue", "list", "--repo", repo, "--label", label, "--state", "open", "--limit", "50",
                     "--json", "number,title,body,url,author"]) or []:
        key = f"issue:{issue['number']}"
        if key not in seen:
            created.append(_issue_draft(project, issue, moment))
            seen.add(key)
    branch = settings.get("branch") or "main"
    newest = {}
    for run in gh(["run", "list", "--repo", repo, "--branch", branch, "--limit", "30",
                   "--json", "databaseId,workflowName,conclusion,headSha,url,createdAt"]) or []:
        if run.get("conclusion") and run["workflowName"] not in newest:
            newest[run["workflowName"]] = run  # gh lists newest first
    for run in newest.values():
        key = f"run:{run['databaseId']}"
        if run["conclusion"] == "failure" and key not in seen:
            created.append(_ci_draft(project, run, branch, moment))
            seen.add(key)
    allowed = set(settings.get("allowed_authors") or [])
    since = state.get("comments_since") or (moment.isoformat(timespec="seconds"))
    for comment in gh(["api", f"repos/{repo}/issues/comments?since={since}&per_page=100"]) or []:
        key = f"comment:{comment['id']}"
        match = COMMENT.match((comment.get("body") or "").strip())
        if key in seen or not match:
            continue
        seen.add(key)
        author = (comment.get("user") or {}).get("login")
        if author not in allowed:
            ignored.append({"comment": comment.get("html_url"), "author": author,
                            "reason": "作者不在 github.allowed_authors 中"})
            continue
        revised.append(_revise(project, match.group(1), match.group(2).strip()[:2000], author, revise))
    write_json(project / STATE, {"version": 1, "seen": sorted(seen),
                                 "comments_since": moment.isoformat(timespec="seconds"),
                                 "synced_at": moment.isoformat(timespec="seconds")}, max_bytes=2_000_000)
    return {"status": "GITHUB_SYNCED", "project": str(project), "repo": repo, "created": created,
            "revised": revised, "ignored": ignored,
            "next": f"storyloop inbox {project}" if created or revised else None}


def _issue_draft(project: Path, issue: dict, moment: datetime) -> dict:
    idea = f"GitHub #{issue['number']}: {_line(issue['title'])}"
    problem = _line(issue.get("body") or issue["title"])[:600]
    command = ["storyloop", "assess", str(project), "--idea", idea, "--problem", problem,
               "--success", "<凭什么知道解决了>", "--if-not", "<不做会怎样>", "--decision", "<do|clarify|drop>"]
    return _write(project, f"EVT-issue-{issue['number']}", {
        "kind": "issue", "source": issue.get("url"), "author": (issue.get("author") or {}).get("login"),
        "title": _line(issue["title"]), "body": redact(issue.get("body") or "")[:MAX_TEXT],
        "idea": idea, "next": shlex.join(command)}, moment)


def _ci_draft(project: Path, run: dict, branch: str, moment: datetime) -> dict:
    sha = str(run.get("headSha") or "")[:7]
    idea = f"修复 CI：{_line(run['workflowName'])} 在 {branch} 上失败（{sha}）"
    command = ["storyloop", "assess", str(project), "--idea", idea,
               "--problem", f"{branch} 上 {run['workflowName']} 失败：{run.get('url')}",
               "--success", f"{run['workflowName']} 在 {branch} 上恢复为绿", "--if-not", "<不修会怎样>",
               "--decision", "<do|clarify|drop>"]
    return _write(project, f"EVT-ci-{run['databaseId']}", {
        "kind": "ci_red", "source": run.get("url"), "title": idea, "idea": idea, "next": shlex.join(command)}, moment)


def _write(project: Path, identifier: str, body: dict, moment: datetime) -> dict:
    record = {"version": 1, "id": identifier, "at": moment.isoformat(timespec="seconds"), **body}
    write_json(project / DRAFTS / f"{identifier}.json", record, max_bytes=64_000)
    return {"id": identifier, "kind": body["kind"], "title": body["title"]}


def _revise(project: Path, change_id: str, message: str, author: str, revise) -> dict:
    from .changes import load_change
    try:
        change = load_change(project, change_id)
    except StoryLoopError:
        return {"change_id": change_id, "status": "UNKNOWN_CHANGE"}
    if change["status"] != "DRAFT" or revise is None:
        return {"change_id": change_id, "status": "NOT_REVISED", "reason": f"变更状态为 {change['status']}，只修订 DRAFT"}
    from argparse import Namespace
    from .touch_ledger import record_touch
    record_touch(Namespace(command="change", project=project, change_id=change_id, message=message, supersede=None))
    revise(project, change_id, f"{message}\n\n（来自 GitHub 评论，作者 {author}）", [])
    return {"change_id": change_id, "status": "REVISED", "author": author}


def pending_events(project: Path) -> list[dict]:
    """Event drafts a person has not handled: no assessment with that idea yet, not dismissed."""
    directory = Path(project) / DRAFTS
    if not directory.is_dir():
        return []
    ideas = set()
    for path in (Path(project) / ".storyloop/assessments").glob("*.json"):
        try:
            ideas.add(load_json(path).get("idea"))
        except (OSError, ValueError, StoryLoopError):
            continue
    items = []
    for path in sorted(directory.glob("EVT-*.json")):
        try:
            record = load_json(path)
        except (OSError, ValueError, StoryLoopError):
            continue
        if record.get("dismissed") or record.get("idea") in ideas:
            continue
        items.append(record)
    return items


def dismiss_event(project: Path, identifier: str) -> dict:
    require(re.fullmatch(r"EVT-[a-z_]+-\d+", identifier or "") is not None, "Invalid event id")
    path = Path(project) / DRAFTS / f"{identifier}.json"
    require(path.is_file(), f"Event {identifier} does not exist")
    write_json(path, {**load_json(path), "dismissed": True}, max_bytes=64_000)
    return {"status": "EVENT_DISMISSED", "event": identifier}


def render_sync(result: dict) -> str:
    lines = [f"GITHUB_SYNCED  {result['repo']}：新事件 {len(result['created'])}，修订 {len(result['revised'])}"]
    lines += [f"  {item['id']}  {item['title']}" for item in result["created"]]
    lines += [f"  {item['change_id']}  {item['status']}" for item in result["revised"]]
    lines += [f"  忽略评论 {item['comment']}（{item['author']}）：{item['reason']}" for item in result["ignored"]]
    if result.get("next"):
        lines.append(f"next: {result['next']}")
    return "\n".join(lines)


def _line(text: str) -> str:
    return " ".join(redact(str(text)).split())
