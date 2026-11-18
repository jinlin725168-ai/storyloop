"""Roadmap H: run several approved changes at once, each in its own independent clone (worker).

The project stays the router: it holds the drafts, decides which change starts, and lands each
worker's verified result. A worker is an ordinary StoryLoop project, a `git clone --no-local` of
the project's branch with a copy of its control plane, so every gate runs in it unchanged.
Linked worktrees stay unsupported (the engine needs a local `.git` directory).

The watchdog is part of `workers status`: a worker that is alive but has written nothing for
`watchdog_idle_minutes`, or has run longer than `watchdog_wall_minutes`, or has spent more than
`watchdog_cost_usd`, is flagged so a person can decide to stop it.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import subprocess
import sys

from .baseline import _exclude_control_plane, _git, load_baseline, load_project_config
from .changes import load_change
from .models import StoryLoopError, require
from .storage import write_json
from .worker_registry import (_alive, _copy_control_plane, _entries, _entry_path, _worker_status)

DEFAULTS = {"workers_max": 3, "watchdog_idle_minutes": 30, "watchdog_wall_minutes": 180}
NO_TOUCH = "STORYLOOP_NO_TOUCH"  # the worker's own engine commands are not human touches


def add_workers_arguments(sub) -> None:
    workers = sub.add_parser("workers", help="并行：每个变更在独立 clone 里跑闭环（start/status/stop/land），含 Watchdog")
    actions = workers.add_subparsers(dest="workers_command", required=True)
    for name, text in (("start", "为一个变更建 worker 并在后台跑闭环（DRAFT 时等同批准）"),
                       ("status", "列出 worker、运行状态、费用与 Watchdog 标记"),
                       ("stop", "中断 worker 的运行（保存状态，可再 start 续跑）"),
                       ("land", "把 worker 已通过的结果带回项目：未分叉则快进，已分叉则合并后复验")):
        child = actions.add_parser(name, help=text)
        child.add_argument("project", type=Path)
        if name != "status":
            child.add_argument("--change", dest="change_id", required=True)
        child.add_argument("--json", action="store_true", help="输出完整 JSON")


def dispatch_workers(args) -> dict:
    project = args.project.resolve()
    if args.workers_command == "start":
        return start_worker(project, args.change_id)
    if args.workers_command == "stop":
        return stop_worker(project, args.change_id)
    if args.workers_command == "land":
        from .worker_land import land_worker
        return land_worker(project, args.change_id)
    return workers_status(project)


def render_workers(result: dict) -> str:
    rows = result["workers"]
    if not rows:
        return "WORKERS  没有 worker：storyloop workers start <项目> --change <ID>"
    lines = [f"WORKERS  {sum(row['state'] == 'running' for row in rows)} 个运行中，共 {len(rows)} 个"]
    for row in rows:
        cost = f"${row['cost_usd']:.2f}" if isinstance(row["cost_usd"], (int, float)) else "费用未知"
        flags = f"  ⚠ {'、'.join(FLAG_LABELS[flag] for flag in row['flags'])}" if row["flags"] else ""
        lines.append(f"  {row['change_id']}  {row['state']}  {row['run_status']}  "
                     f"{row['wall_minutes'] or 0} 分钟  {cost}{flags}")
        if row.get("next"):
            lines.append(f"    下一步：{row['next']}")
    return "\n".join(lines)


FLAG_LABELS = {"STALLED": "长时间无活动", "OVER_TIME": "超出墙钟上限", "OVER_BUDGET": "超出费用上限"}


def workers_root(project: Path, config: dict) -> Path:
    configured = config.get("workers_root")
    if isinstance(configured, str) and configured.strip():
        return Path(configured).expanduser().resolve()
    return project.parent / f".{project.name}-storyloop-workers"


def start_worker(project: Path, change_id: str, *, launcher=None) -> dict:
    """Clone the project for one change and run its loop there in the background."""
    project = Path(project).resolve()
    config = load_project_config(project)
    change = load_change(project, change_id)
    require(change["status"] != "PASSED", f"{change_id} has already passed")
    entries = _entries(project)
    previous = entries.get(change_id)
    require(not (previous and _alive(previous.get("pid"))), f"{change_id} already has a running worker")
    running = [entry for entry in entries.values() if entry["state"] == "running" and _alive(entry.get("pid"))]
    limit = config.get("workers_max", DEFAULTS["workers_max"])
    require(len(running) < limit, f"{len(running)} workers are running (workers_max={limit}); "
                                  "land or stop one first")
    target = workers_root(project, config) / change_id
    if previous and previous["state"] != "landed" and target.is_dir():
        base_head = previous["base_head"]  # resume the same worker after a stop
    else:
        require(not target.exists(), f"Worker directory already exists: {target}")
        require(not _git(project, "status", "--porcelain=v1", "--untracked-files=all").strip(),
                "Git worktree must be clean before starting a worker")
        base_head = _clone(project, target)
    status = _worker_status(target, change_id)
    command = "approve" if status == "DRAFT" else "run"
    argv = (launcher or _launcher)(command, target, change_id)
    pid = _spawn(argv, target)
    entry = {"version": 1, "change_id": change_id, "path": str(target), "pid": pid, "command": command,
             "base_head": base_head, "started_at": _now(), "state": "running"}
    write_json(_entry_path(project, change_id), entry, max_bytes=64_000)
    return {"status": "WORKER_STARTED", "change_id": change_id, "worker": str(target), "pid": pid,
            "next": f"storyloop workers status {project}"}


def _clone(project: Path, target: Path) -> str:
    branch = load_baseline(project)["source_branch"]
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", "--no-local", "--branch", branch, str(project), str(target)],
                   check=True, capture_output=True)
    for key in ("user.name", "user.email"):
        value = subprocess.run(["git", "-C", str(project), "config", "--local", "--get", key],
                               capture_output=True, text=True).stdout.strip()
        if value:
            subprocess.run(["git", "-C", str(target), "config", key, value], check=True)
    _copy_control_plane(project / ".storyloop", target / ".storyloop")
    _exclude_control_plane(target)
    return _git(target, "rev-parse", "HEAD").strip()


def _launcher(command: str, target: Path, change_id: str) -> list[str]:
    return [sys.executable, "-m", "storyloop", command, str(target), "--change", change_id, "--no-interactive"]


def _spawn(argv: list[str], target: Path) -> int:
    log = (target / ".storyloop/worker.log").open("ab")
    options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
               if os.name == "nt" else {"start_new_session": True})
    process = subprocess.Popen(argv, cwd=target, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                               env={**os.environ, NO_TOUCH: "1"}, **options)
    log.close()
    return process.pid


def workers_status(project: Path, now: datetime | None = None) -> dict:
    """Every worker with its run status, cost and watchdog flags, and what to do next."""
    project = Path(project).resolve()
    config = load_project_config(project)
    rows = [_row(project, entry, config, now or datetime.now(timezone.utc))
            for entry in sorted(_entries(project).values(), key=lambda item: item["started_at"])]
    return {"status": "WORKERS", "project": str(project), "workers": rows}


def _row(project: Path, entry: dict, config: dict, now: datetime) -> dict:
    target, change_id = Path(entry["path"]), entry["change_id"]
    alive = entry["state"] == "running" and _alive(entry.get("pid"))
    status = _worker_status(target, change_id) if target.is_dir() else "MISSING"
    wall = _minutes(entry["started_at"], now)
    idle = _minutes(_last_activity(target), now) if target.is_dir() else None
    cost = _cost(target, change_id)
    flags = []
    if alive:
        if idle is not None and idle > config.get("watchdog_idle_minutes", DEFAULTS["watchdog_idle_minutes"]):
            flags.append("STALLED")
        if wall is not None and wall > config.get("watchdog_wall_minutes", DEFAULTS["watchdog_wall_minutes"]):
            flags.append("OVER_TIME")
        limit = config.get("watchdog_cost_usd")
        if isinstance(limit, (int, float)) and isinstance(cost, (int, float)) and cost > limit:
            flags.append("OVER_BUDGET")
    state = entry["state"] if entry["state"] == "landed" else "running" if alive else "stopped"
    where = f"{project} --change {change_id}"
    if flags:
        next_step = f"看 {target}/.storyloop/worker.log 后决定：storyloop workers stop {where}，或继续等待"
    elif state == "stopped" and status == "PASSED":
        next_step = f"storyloop workers land {where}"
    elif state == "stopped":
        next_step = f"在 worker 中处理停点（storyloop status {target} --change {change_id}），再 storyloop workers start {where}"
    else:
        next_step = None
    return {"change_id": change_id, "worker": str(target), "state": state, "run_status": status,
            "pid": entry.get("pid"), "wall_minutes": wall, "idle_minutes": idle, "cost_usd": cost,
            "flags": flags, "landed_head": entry.get("landed_head"), "next": next_step}


def stop_worker(project: Path, change_id: str) -> dict:
    """Interrupt a worker's loop (SIGINT, so the engine saves its state); resume with `workers start`."""
    project = Path(project).resolve()
    entry = _entries(project).get(change_id)
    require(entry is not None, f"No worker for {change_id}")
    pid = entry.get("pid")
    if _alive(pid):
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.killpg(os.getpgid(pid), signal.SIGINT)
    write_json(_entry_path(project, change_id), {**entry, "state": "stopped"}, max_bytes=64_000)
    return {"status": "WORKER_STOPPED", "change_id": change_id, "worker": entry["path"],
            "next": f"storyloop workers start {project} --change {change_id}"}


def worker_note(project: Path, change_id: str) -> dict | None:
    """For the project overview: a change that is being worked on in a worker."""
    entry = _entries(Path(project)).get(change_id)
    if entry is None or entry["state"] == "landed":
        return None
    return {"worker": entry["path"], "alive": entry["state"] == "running" and _alive(entry.get("pid"))}


def _last_activity(target: Path) -> str | None:
    newest = 0.0
    for root in (target / ".storyloop/state", target / ".storyloop/worker.log"):
        paths = [root] if root.is_file() else root.rglob("*") if root.is_dir() else []
        for path in paths:
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                continue
    return datetime.fromtimestamp(newest, timezone.utc).isoformat() if newest else None


def _cost(target: Path, change_id: str) -> float | None:
    try:
        from .telemetry import summarize
        value = summarize(target, change_id).get("known_cost_usd")
    except (OSError, ValueError, StoryLoopError):
        return None
    return value if isinstance(value, (int, float)) else None


def _minutes(stamp: str | None, now: datetime) -> int | None:
    try:
        then = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((now - then).total_seconds() // 60))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
