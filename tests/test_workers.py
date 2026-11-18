"""Roadmap H: changes run in parallel in their own clones and land only as verified."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from storyloop.baseline import bootstrap_project, load_project_config
from storyloop.models import StoryLoopError
from storyloop.storage import write_json
from tests.routing_support import legacy_create_change as create_change
from tests.support import product
from tests.test_baseline_behavior import git, repository

SLEEP = [sys.executable, "-c", "import time; time.sleep(30)"]
QUICK = [sys.executable, "-c", "pass"]


def _project(folder: Path) -> Path:
    root = folder / "project"
    root.mkdir()
    repository(root)
    bootstrap_project(root, {"test_commands": []})
    config = load_project_config(root)
    write_json(root / ".storyloop/config.json", {**config, "workers_root": str(folder / "workers")})
    return root


def _wait(project: Path, change_id: str, seconds: float = 180) -> dict:
    from storyloop.workers import workers_status
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        row = next(row for row in workers_status(project)["workers"] if row["change_id"] == change_id)
        if row["state"] != "running":
            return row
        time.sleep(0.5)
    raise AssertionError(f"worker {change_id} did not finish")


def _fake_pass(project: Path, change_id: str, path: str, content: str) -> None:
    """Stand in for a worker's finished loop: one commit on its branch and a PASSED run."""
    from storyloop.worker_registry import _entries
    worker = Path(_entries(project)[change_id]["path"])
    (worker / path).parent.mkdir(parents=True, exist_ok=True)
    (worker / path).write_text(content, encoding="utf-8")
    git(worker, "add", path)
    git(worker, "commit", "-q", "-m", f"feat({change_id})")
    write_json(worker / ".storyloop/state/runs" / f"{change_id}.json", {"status": "PASSED", "change_id": change_id})


class WorkerLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()
        self.root = _project(self.folder)

    def _start(self, change_id, argv):
        from storyloop.workers import start_worker
        return start_worker(self.root, change_id, launcher=lambda command, target, change: argv)

    def test_a_worker_is_an_independent_clone_with_the_control_plane_and_a_clean_tree(self):
        change_id = create_change(self.root, "idea", product())["change_id"]
        result = self._start(change_id, QUICK)
        worker = Path(result["worker"])
        self.assertEqual(result["status"], "WORKER_STARTED")
        self.assertTrue((worker / ".git").is_dir())
        self.assertTrue((worker / ".storyloop/state/changes" / f"{change_id}.json").is_file())
        self.assertEqual(git(worker, "status", "--porcelain").strip(), "")
        self.assertEqual(git(worker, "rev-parse", "HEAD"), git(self.root, "rev-parse", "HEAD"))
        self.assertIn(".storyloop/", (worker / ".git/info/exclude").read_text(encoding="utf-8"))
        self.assertEqual(git(self.root, "status", "--porcelain").strip(), "")
        self.assertEqual(_wait(self.root, change_id)["state"], "stopped")

    def test_workers_max_limits_running_workers_and_stop_interrupts_one(self):
        from storyloop.workers import stop_worker, workers_status
        config = load_project_config(self.root)
        write_json(self.root / ".storyloop/config.json", {**config, "workers_max": 1})
        first = create_change(self.root, "one", product())["change_id"]
        second = create_change(self.root, "two", product())["change_id"]
        self._start(first, SLEEP)
        with self.assertRaises(StoryLoopError) as refused:
            self._start(second, QUICK)
        self.assertIn("workers_max=1", str(refused.exception))
        self.assertEqual(stop_worker(self.root, first)["status"], "WORKER_STOPPED")
        row = workers_status(self.root)["workers"][0]
        self.assertEqual(row["state"], "stopped")
        self.assertIn("workers start", row["next"])

    def test_the_watchdog_flags_a_silent_or_overlong_worker(self):
        from storyloop.worker_registry import _entries, save_entry
        from storyloop.workers import workers_status
        change_id = create_change(self.root, "idea", product())["change_id"]
        self._start(change_id, SLEEP)
        self.addCleanup(lambda: __import__("storyloop.workers", fromlist=["x"]).stop_worker(self.root, change_id))
        entry = _entries(self.root)[change_id]
        save_entry(self.root, {**entry, "started_at": (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()})
        old = time.time() - 3 * 3600
        for path in Path(entry["path"], ".storyloop").rglob("*"):
            os.utime(path, (old, old))
        row = workers_status(self.root)["workers"][0]
        self.assertEqual(row["state"], "running")
        self.assertEqual(row["flags"], ["STALLED", "OVER_TIME"])
        self.assertIn("workers stop", row["next"])

    def test_landing_onto_a_moved_branch_stops_on_a_conflict(self):
        from storyloop.worker_land import land_worker
        change_id = create_change(self.root, "idea", product())["change_id"]
        self._start(change_id, QUICK)
        _wait(self.root, change_id)
        _fake_pass(self.root, change_id, "modules/shared.py", "VALUE = 'worker'\n")
        (self.root / "modules").mkdir(exist_ok=True)
        (self.root / "modules/shared.py").write_text("VALUE = 'main'\n", encoding="utf-8")
        git(self.root, "add", "modules/shared.py")
        git(self.root, "commit", "-q", "-m", "another change landed first")
        head = git(self.root, "rev-parse", "HEAD")
        result = land_worker(self.root, change_id)
        self.assertEqual(result["status"], "LAND_CONFLICT")
        self.assertEqual(result["conflicts"], ["modules/shared.py"])
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), head)  # nothing forced

    def test_landing_onto_a_moved_branch_stops_when_reverification_fails(self):
        from storyloop.worker_land import land_worker
        check = ("import pathlib, sys; a = pathlib.Path('modules/a.py').exists(); "
                 "b = pathlib.Path('modules/b.py').exists(); sys.exit(1 if a and b else 0)")
        config = load_project_config(self.root)
        config["profile"]["unit_commands"] = [["{python}", "-c", check]]
        write_json(self.root / ".storyloop/config.json", config)
        change_id = create_change(self.root, "idea", product())["change_id"]
        self._start(change_id, QUICK)
        _wait(self.root, change_id)
        _fake_pass(self.root, change_id, "modules/b.py", "B = 1\n")
        (self.root / "modules").mkdir(exist_ok=True)
        (self.root / "modules/a.py").write_text("A = 1\n", encoding="utf-8")
        git(self.root, "add", "modules/a.py")
        git(self.root, "commit", "-q", "-m", "another change landed first")
        result = land_worker(self.root, change_id)
        self.assertEqual(result["status"], "LAND_REVERIFY_FAILED")
        self.assertIn("unit", result["failed"])

    def test_a_stopped_worker_reaches_the_inbox_and_the_overview(self):
        from storyloop.cli import dispatch, parser
        change_id = create_change(self.root, "idea", product())["change_id"]
        self._start(change_id, QUICK)
        _wait(self.root, change_id)
        overview = dispatch(parser().parse_args(["status", str(self.root)]))
        row = next(item for item in overview["changes"] if item["change_id"] == change_id)
        self.assertEqual((row["status"], row["waiting"]), ("IN_WORKER", "human"))
        inbox = dispatch(parser().parse_args(["inbox", str(self.root)]))
        item = next(item for item in inbox["items"] if item["change_id"] == change_id)
        self.assertEqual(item["status"], "DRAFT")
        self.assertIn("在 worker 中处理停点", item["next"])

    def test_land_refuses_a_worker_that_has_not_passed(self):
        from storyloop.worker_land import land_worker
        change_id = create_change(self.root, "idea", product())["change_id"]
        self._start(change_id, QUICK)
        _wait(self.root, change_id)
        with self.assertRaises(StoryLoopError) as refused:
            land_worker(self.root, change_id)
        self.assertIn("not PASSED", str(refused.exception))


class ParallelLoopTests(unittest.TestCase):
    """Two changes run their whole loop in workers (simulated adapter, real Git and engine)."""

    def test_two_workers_pass_and_land_one_by_fast_forward_and_one_by_reverification(self):
        from storyloop.cli import dispatch, parser
        from storyloop.workers import workers_status
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder).resolve()
            root = _project(folder)
            first = create_change(root, "first", product())["change_id"]
            second = create_change(root, "second", product())["change_id"]
            _adapter(root, folder / "first", first, "modules/first.py")
            dispatch(parser().parse_args(["workers", "start", str(root), "--change", first]))
            _adapter(root, folder / "second", second, "modules/second.py")
            dispatch(parser().parse_args(["workers", "start", str(root), "--change", second]))
            for change_id in (first, second):
                row = _wait(root, change_id)
                log = (Path(row["worker"]) / ".storyloop/worker.log").read_text(encoding="utf-8")
                self.assertEqual(row["run_status"], "PASSED", log[-2000:])
                self.assertIn("workers land", row["next"])
            landed = dispatch(parser().parse_args(["workers", "land", str(root), "--change", first]))
            self.assertEqual((landed["status"], landed["verification"]), ("LANDED", "exact"))
            landed = dispatch(parser().parse_args(["workers", "land", str(root), "--change", second]))
            self.assertEqual((landed["status"], landed["verification"]), ("LANDED", "reverified"))
            self.assertIn(f"Merge {second}", git(root, "log", "-1", "--format=%s"))
            self.assertTrue((root / "modules/first.py").is_file() and (root / "modules/second.py").is_file())
            self.assertEqual(git(root, "status", "--porcelain", "--untracked-files=all").strip(), "")
            for change_id in (first, second):
                self.assertTrue((root / "reports/storyloop" / change_id / "report.md").is_file())
                run = json.loads((root / ".storyloop/state/runs" / f"{change_id}.json").read_text(encoding="utf-8"))
                self.assertEqual(run["status"], "PASSED")
            overview = dispatch(parser().parse_args(["status", str(root)]))
            self.assertEqual(overview["counts"]["done"], 2)
            self.assertEqual({row["state"] for row in workers_status(root)["workers"]}, {"landed"})


def _adapter(root: Path, folder: Path, change_id: str, created: str) -> None:
    """Point the project at a scripted adapter for the next worker (the clone copies the config)."""
    folder.mkdir()
    source = ("from pathlib import Path\n"
              f"raise SystemExit(0 if Path({created!r}).is_file() else 1)\n")
    telemetry = {"model": "fixture-model", "vendor": "fixture-vendor", "tier_applied": "fixture-tier", "turns": 1}
    calls = [
        {"role": "acceptance", "result": {"files": [{"path": "flow.py", "content": source}],
         "manifest": {"version": 1, "tests": [{"id": "SMOKE-1", "criteria": ["AC-1"],
             "command": ["{python}", f"acceptance/changes/{change_id}/flow.py"], "timeout_seconds": 10}]}},
         "telemetry": telemetry},
        {"role": "implement", "creates": [{"path": created, "content": "VALUE = 1\n"}],
         "result": {"summary": "fixture implementation", "questions": [],
                    "facts": [{"statement": "file written", "location": f"{created}:1"}]},
         "telemetry": telemetry},
        {"role": "review", "result": {"verdict": "pass", "summary": "fixture review", "findings": [],
                                      "questions": [], "facts": []}, "telemetry": telemetry},
    ]
    script = folder / "script.json"
    write_json(script, {"calls": calls})
    adapter = Path(__file__).resolve().parents[1] / "storyloop/testing/mock_adapter.py"
    config = load_project_config(root)
    config.update({"adapter": "command", "agent_command": [sys.executable, str(adapter), "--script", str(script)]})
    write_json(root / ".storyloop/config.json", config)


if __name__ == "__main__":
    unittest.main()
